"""Klimathemmet AI Studio – webbserver.

Starta: uvicorn app.main:app --reload
"""

import asyncio
import logging
import tempfile
from contextlib import asynccontextmanager, suppress
from datetime import date, time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator
from starlette.background import BackgroundTask

from . import auth, config, db, jobb, lankforslag, publicering, saknade, sajtkarta, seo_ai
from .planering import MAX_SEKUNDARA, CsvFel, liknande, normalisera_sokord, tolka_csv
from .publicering import PubliceringsFel
from .render import wordpress_block
from .wordpress import WordPress, WordPressFel

STATIC = Path(__file__).resolve().parent / "static"
MAX_CSV_BYTES = 2 * 1024 * 1024


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
# httpx loggar varje anrop; adresserna innehåller inga hemligheter men blir brus.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.settings.kontrollera_produktion()
    db.init()
    publicering.init()
    sajtkarta.init()
    seo_ai.init()
    lankforslag.init()
    saknade.init()
    uppgift = asyncio.create_task(publicering.schemalaggare()) if config.settings.schemalaggare else None
    yield
    if uppgift:
        uppgift.cancel()
        with suppress(asyncio.CancelledError):
            await uppgift


app = FastAPI(title="Klimathemmet AI Studio", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
app.middleware("http")(auth.middleware)


# ---------- Inloggning ----------

class Inloggning(BaseModel):
    anvandare: str = Field(max_length=200)
    losenord: str = Field(max_length=500)


@app.get("/login")
def login_sida():
    return FileResponse(STATIC / "login.html")


@app.post("/api/login")
def logga_in(body: Inloggning, request: Request):
    if not config.settings.inloggning_aktiv:
        return {"ok": True}
    ip = auth.klient_ip(request)
    if auth.for_manga_forsok(ip):
        raise HTTPException(429, "För många misslyckade försök. Vänta en kvart och försök igen.")
    if not auth.kontrollera_losenord(ip, body.anvandare, body.losenord):
        raise HTTPException(401, "Fel användarnamn eller lösenord.")
    resp = JSONResponse({"ok": True})
    auth.satt_kaka(resp, config.settings.studio_user)
    return resp


@app.post("/api/logout")
def logga_ut():
    resp = JSONResponse({"ok": True})
    auth.rensa_kaka(resp)
    return resp


@app.get("/healthz")
def halsa():
    return {"ok": True}


class Sokord(BaseModel):
    huvudsokord: str = Field(min_length=2, max_length=120)
    sekundara_sokord: list[str] = Field(default_factory=list, max_length=MAX_SEKUNDARA)

    @field_validator("huvudsokord")
    @classmethod
    def _huvud(cls, v: str) -> str:
        v = normalisera_sokord(v)
        if len(v) < 2:
            raise ValueError("för kort")
        return v

    @field_validator("sekundara_sokord")
    @classmethod
    def _sek(cls, v: list[str]) -> list[str]:
        ut: list[str] = []
        for s in (normalisera_sokord(x)[:120] for x in v):
            if s and s.lower() not in {u.lower() for u in ut}:
                ut.append(s)
        return ut


class Bild(BaseModel):
    id: int
    alt: str = ""
    bildtext: str = ""
    prompt: str = ""
    url: str | None = None


class Article(BaseModel):
    titel: str
    slug: str
    meta_titel: str
    meta_beskrivning: str
    html: str
    bilder: list[Bild] = Field(default_factory=list)
    huvudsokord: str = ""
    sekundara_sokord: list[str] = Field(default_factory=list)
    sokintention: dict | None = None
    internlankar: list[dict] = Field(default_factory=list)
    demo: bool = False


class Ordning(BaseModel):
    ids: list[int]


def _hamta_eller_404(artikel_id: int) -> dict:
    artikel = db.hamta(artikel_id)
    if artikel is None:
        raise HTTPException(404, "Artikeln finns inte.")
    return artikel


def _med_extra(artikel: dict, lika: dict[int, list[dict]]) -> dict:
    artikel["liknande"] = lika.get(artikel["id"], [])
    artikel["forlopp"] = jobb.forlopp.get(artikel["id"])
    return artikel


# ---------- Status och export ----------

@app.get("/api/status")
def status():
    return config.settings.status()


@app.post("/api/exportera")
def exportera(article: Article):
    return {"kod": wordpress_block(article.model_dump())}


@app.post("/api/wordpress/kontrollera")
async def kontrollera_wordpress():
    """Undersöker anslutningen, behörigheter, SEO-plugin och tillgängliga fält. Ändrar ingenting."""
    try:
        async with WordPress() as wp:
            return await wp.kontrollera()
    except WordPressFel as e:
        raise HTTPException(502, str(e))


@app.post("/api/artiklar/{artikel_id}/wp-utkast")
async def skicka_som_utkast(artikel_id: int):
    """Skapar eller uppdaterar artikeln som utkast i WordPress, på din begäran. Publicerar aldrig."""
    artikel = _hamta_eller_404(artikel_id)
    if not artikel["har_innehall"] or artikel["status"] in ("genereras", "publicerad"):
        raise HTTPException(409, "Artikeln kan inte skickas som utkast i sin nuvarande status.")
    try:
        async with WordPress() as wp:
            post = await wp.spara_inlagg(artikel, "draft")
    except WordPressFel as e:
        raise HTTPException(502, str(e))
    if post.get("status") == "publish":
        raise HTTPException(409, "Artikeln är redan publicerad i WordPress.")
    db.uppdatera(artikel_id, wp_post_id=post["id"])
    db.logga(artikel_id, "wp_utkast_skapat", f"Utkast i WordPress (inlägg {post['id']})")
    return {"wp_post_id": post["id"]}


# ---------- Artikelkö ----------

@app.get("/api/artiklar")
def lista_artiklar():
    artiklar = db.lista()
    lika = liknande(artiklar)
    return {"artiklar": [_med_extra(a, lika) for a in artiklar]}


@app.post("/api/artiklar", status_code=201)
def skapa_artikel(sokord: Sokord):
    artikel_id = db.skapa(sokord.huvudsokord, sokord.sekundara_sokord)
    return db.hamta(artikel_id)


@app.post("/api/artiklar/import")
async def importera_csv(request: Request):
    data = await request.body()
    if len(data) > MAX_CSV_BYTES:
        raise HTTPException(413, "Filen är för stor (max 2 MB).")
    try:
        rader = tolka_csv(data)
    except CsvFel as e:
        raise HTTPException(400, str(e))
    befintliga = {a["huvudsokord"].lower() for a in db.lista()}
    nya, dubbletter, sedda = [], [], set()
    for huvud, sek in rader:
        nyckel = huvud.lower()
        if nyckel in befintliga or nyckel in sedda:
            dubbletter.append(huvud)
            continue
        sedda.add(nyckel)
        nya.append((huvud, sek))
    ids = db.skapa_manga(nya) if nya else []
    return {"importerade": len(ids), "dubbletter": dubbletter}


@app.put("/api/artiklar/ordning")
def andra_ordning(ordning: Ordning):
    db.satt_ordning(ordning.ids)
    return {"ok": True}


@app.post("/api/artiklar/generera-alla")
async def generera_alla():
    """Genererar alla utkast som saknar innehåll. Resultaten hamnar i Väntar på granskning."""
    startade = [
        a["id"] for a in db.lista()
        if a["status"] == "utkast" and not a["har_innehall"] and jobb.starta(a["id"])
    ]
    return {"startade": len(startade)}


@app.get("/api/artiklar/{artikel_id}")
def hamta_artikel(artikel_id: int):
    artikel = _hamta_eller_404(artikel_id)
    artikel = _med_extra(artikel, liknande(db.lista()))
    artikel["historik"] = db.historik(artikel_id)
    artikel["lankplan"] = seo_ai.lankplan(artikel_id)
    artikel["lankkontroll"] = seo_ai.lankkontroll((artikel["innehall"] or {}).get("html", ""))
    artikel["inkommande_forslag"] = lankforslag.for_artikel(artikel_id)
    return artikel


LASTA = ("genereras", "schemalagd", "publicerad")


@app.patch("/api/artiklar/{artikel_id}/sokord")
def andra_sokord(artikel_id: int, sokord: Sokord):
    artikel = _hamta_eller_404(artikel_id)
    if artikel["status"] in LASTA:
        raise HTTPException(409, "Sökorden kan inte ändras medan artikeln genereras, är schemalagd eller publicerad.")
    db.uppdatera(artikel_id, huvudsokord=sokord.huvudsokord, sekundara=sokord.sekundara_sokord)
    return db.hamta(artikel_id)


def _avboka_om_schemalagd(artikel: dict, orsak: str) -> dict:
    """En schemalagd artikel som ska ändras tas först bort ur kalendern."""
    if artikel["status"] != "schemalagd":
        return artikel
    try:
        publicering.avboka(artikel["id"], orsak)
    except PubliceringsFel as e:
        raise HTTPException(409, str(e))
    return db.hamta(artikel["id"])


@app.put("/api/artiklar/{artikel_id}/innehall")
def spara_innehall(artikel_id: int, article: Article):
    artikel = _hamta_eller_404(artikel_id)
    if artikel["status"] == "schemalagd":
        jamfor = Article(**artikel["innehall"]).model_dump() | {
            "huvudsokord": artikel["huvudsokord"], "sekundara_sokord": artikel["sekundara_sokord"]}
        if article.model_dump() | {"huvudsokord": artikel["huvudsokord"], "sekundara_sokord": artikel["sekundara_sokord"]} != jamfor:
            artikel = _avboka_om_schemalagd(artikel, "Artikeln redigerades efter godkännandet; publiceringen stoppades.")
    if artikel["status"] not in ("utkast", "granskas", "godkand", "schemalagd") or not artikel["har_innehall"]:
        raise HTTPException(409, "Artikeln kan inte redigeras i sin nuvarande status.")
    innehall = article.model_dump()
    innehall["huvudsokord"] = artikel["huvudsokord"]
    innehall["sekundara_sokord"] = artikel["sekundara_sokord"]
    if artikel.get("mal_slug"):
        # Artikeln är skapad för en adress som befintliga artiklar redan länkar till.
        innehall["slug"] = artikel["mal_slug"]
    if innehall == Article(**artikel["innehall"]).model_dump() | {
        "huvudsokord": artikel["huvudsokord"], "sekundara_sokord": artikel["sekundara_sokord"]
    }:
        return {"status": artikel["status"]}
    # En godkänd artikel som ändras måste godkännas igen innan den kan schemaläggas.
    ny_status = "granskas" if artikel["status"] == "godkand" else artikel["status"]
    if not db.byt_status(artikel_id, (artikel["status"],), ny_status, innehall=innehall, godkand_at=None, godkand_hash=None):
        raise HTTPException(409, "Artikeln ändrades samtidigt. Ladda om och försök igen.")
    if artikel["status"] == "godkand":
        db.logga(artikel_id, "godkannande_upphavt", "Artikeln redigerades efter godkännandet")
    return {"status": ny_status}


@app.post("/api/artiklar/{artikel_id}/generera", status_code=202)
async def generera(artikel_id: int):
    artikel = _hamta_eller_404(artikel_id)
    if artikel["status"] not in ("utkast", "granskas", "fel"):
        raise HTTPException(409, "Bara utkast, artiklar som väntar på granskning och artiklar med fel kan genereras.")
    if not jobb.starta(artikel_id):
        raise HTTPException(409, "Artikeln genereras redan.")
    return {"status": "genereras"}


class Kommentar(BaseModel):
    kommentar: str = Field(default="", max_length=4000)


def _byt(artikel_id: int, fran: tuple[str, ...], till: str, handling: str, kommentar: str | None, fel_text: str, **falt):
    artikel = _hamta_eller_404(artikel_id)
    if not db.byt_status(artikel_id, fran, till, **falt):
        raise HTTPException(409, fel_text)
    db.logga(artikel_id, handling, kommentar or None, db.innehall_hash(artikel["innehall"]))
    return {"status": till}


@app.post("/api/artiklar/{artikel_id}/skicka-till-granskning")
def skicka_till_granskning(artikel_id: int):
    if not _hamta_eller_404(artikel_id)["har_innehall"]:
        raise HTTPException(409, "Artikeln har inget innehåll ännu. Generera den först.")
    return _byt(artikel_id, ("utkast",), "granskas", "skickad_till_granskning", None,
                "Bara utkast kan skickas till granskning.")


class Godkannande(BaseModel):
    bekraftad: bool = False


@app.post("/api/artiklar/{artikel_id}/godkann")
def godkann(artikel_id: int, body: Godkannande):
    # Godkännande ska alltid vara ett medvetet steg; gränssnittet skickar bekraftad först efter kryssrutan.
    if not body.bekraftad:
        raise HTTPException(400, "Godkännandet måste bekräftas.")
    artikel = _hamta_eller_404(artikel_id)
    return _byt(artikel_id, ("granskas",), "godkand", "godkand", None,
                "Bara artiklar som väntar på granskning kan godkännas.",
                godkand_at=db.nu(), godkand_hash=db.innehall_hash(artikel["innehall"]))


@app.post("/api/artiklar/{artikel_id}/begar-andringar")
def begar_andringar(artikel_id: int, body: Kommentar):
    kommentar = body.kommentar.strip()
    if not kommentar:
        raise HTTPException(400, "Beskriv vilka ändringar du vill ha.")
    _avboka_om_schemalagd(_hamta_eller_404(artikel_id), "Ändringar begärdes; publiceringen stoppades.")
    return _byt(artikel_id, ("granskas", "godkand"), "utkast", "andringar_begarda", kommentar,
                "Ändringar kan bara begäras för artiklar som väntar på granskning eller är godkända.",
                andringar=kommentar, godkand_at=None, godkand_hash=None)


@app.post("/api/artiklar/{artikel_id}/avvisa")
def avvisa(artikel_id: int, body: Kommentar):
    _avboka_om_schemalagd(_hamta_eller_404(artikel_id), "Artikeln avvisades; publiceringen stoppades.")
    return _byt(artikel_id, ("utkast", "granskas", "godkand", "fel"), "avvisad", "avvisad", body.kommentar.strip(),
                "Artikeln kan inte avvisas i sin nuvarande status.", godkand_at=None, godkand_hash=None)


@app.post("/api/artiklar/{artikel_id}/aterstall")
def aterstall(artikel_id: int):
    return _byt(artikel_id, ("avvisad",), "utkast", "aterstalld", None, "Bara avvisade artiklar kan återställas.")


@app.post("/api/artiklar/{artikel_id}/aterkalla")
def aterkalla(artikel_id: int):
    _avboka_om_schemalagd(_hamta_eller_404(artikel_id), "Godkännandet återkallades; publiceringen stoppades.")
    return _byt(artikel_id, ("godkand",), "granskas", "godkannande_aterkallat", None,
                "Bara godkända artiklar kan återkallas.", godkand_at=None, godkand_hash=None)


@app.delete("/api/artiklar/{artikel_id}", status_code=204)
def ta_bort(artikel_id: int):
    artikel = _hamta_eller_404(artikel_id)
    if artikel["status"] in LASTA:
        raise HTTPException(409, "Artikeln kan inte tas bort medan den genereras, är schemalagd eller publicerad.")
    db.ta_bort(artikel_id)
    db.logga(artikel_id, "borttagen", artikel["huvudsokord"])
    return Response(status_code=204)


# ---------- Publicering ----------

class Schemalaggning(BaseModel):
    startdatum: date
    klockslag: time = publicering.STANDARD_KLOCKSLAG
    artikel_ids: list[int] | None = None


class Flytt(BaseModel):
    datum: date
    klockslag: time | None = None


class Paus(BaseModel):
    pausad: bool


@app.get("/api/publicering")
def publiceringsoversikt():
    artiklar = db.lista()
    return {
        "lage": config.settings.publicering,
        "pausad": publicering.pausad(),
        "wordpress": config.settings.status()["wordpress"],
        "idag": publicering.idag_lokalt().isoformat(),
        "kalender": publicering.kalender(),
        "godkanda": [{"id": a["id"], "huvudsokord": a["huvudsokord"], "titel": a["titel"]}
                     for a in artiklar if a["status"] == "godkand"],
        "logg": db.publiceringslogg(),
    }


@app.post("/api/publicering/schemalagg")
def schemalagg(body: Schemalaggning):
    ids = body.artikel_ids
    if ids is None:
        ids = [a["id"] for a in db.lista() if a["status"] == "godkand"]
    if not ids:
        raise HTTPException(409, "Det finns inga godkända artiklar att schemalägga.")
    try:
        return {"schemalagda": publicering.schemalagg(ids, body.startdatum, body.klockslag)}
    except PubliceringsFel as e:
        raise HTTPException(409, str(e))


@app.post("/api/publicering/{publicering_id}/flytta")
def flytta(publicering_id: int, body: Flytt):
    try:
        return publicering.flytta(publicering_id, body.datum, body.klockslag)
    except PubliceringsFel as e:
        raise HTTPException(409, str(e))


@app.post("/api/artiklar/{artikel_id}/avboka")
def avboka(artikel_id: int):
    _hamta_eller_404(artikel_id)
    try:
        if not publicering.avboka(artikel_id, "Borttagen ur kalendern"):
            raise HTTPException(409, "Artikeln är inte schemalagd.")
    except PubliceringsFel as e:
        raise HTTPException(409, str(e))
    return {"status": "godkand"}


@app.post("/api/publicering/paus")
def pausa(body: Paus):
    publicering.satt_paus(body.pausad)
    return {"pausad": body.pausad}


@app.get("/api/sakerhetskopia")
def ladda_ner_sakerhetskopia():
    fil = Path(tempfile.mkstemp(suffix=".db")[1])
    publicering.sakerhetskopiera(fil)
    namn = f"klimathemmet-studio-{publicering.idag_lokalt().isoformat()}.db"
    return FileResponse(fil, filename=namn, media_type="application/octet-stream",
                        background=BackgroundTask(fil.unlink, missing_ok=True))


# ---------- SEO-karta ----------

class Kommersiell(BaseModel):
    kommersiell: bool


@app.get("/api/seokarta")
def seokarta():
    return {**sajtkarta.analysera(), "forslag": seo_ai.lista_forslag(), "wp_url": config.settings.wp_url,
            "ai": bool(config.settings.anthropic_api_key), "lankforslag_antal": lankforslag.oversikt(),
            "lage": config.settings.publicering}


@app.post("/api/seokarta/kartlagg")
async def kartlagg():
    """Läser hela sajten via REST API. Ändrar ingenting på sajten."""
    try:
        return await sajtkarta.kartlagg()
    except sajtkarta.KartFel as e:
        raise HTTPException(502, str(e))


@app.get("/api/seokarta/sida/{sida_id}")
def seokarta_sida(sida_id: int):
    d = sajtkarta.nod_detaljer(sida_id)
    if d is None:
        raise HTTPException(404, "Sidan finns inte i kartan.")
    return d


@app.post("/api/seokarta/sida/{sida_id}/kommersiell")
def seokarta_kommersiell(sida_id: int, body: Kommersiell):
    try:
        sajtkarta.satt_kommersiell(sida_id, body.kommersiell)
    except sajtkarta.KartFel as e:
        raise HTTPException(404, str(e))
    return {"kommersiell": body.kommersiell}


@app.post("/api/seokarta/forslag")
async def seokarta_forslag():
    try:
        return {"forslag": await seo_ai.foresla_artiklar()}
    except seo_ai.ForslagsFel as e:
        raise HTTPException(409, str(e))


@app.post("/api/seokarta/forslag/{forslag_id}/till-kon", status_code=201)
def seokarta_till_kon(forslag_id: int):
    try:
        return {"artikel_id": seo_ai.till_kon(forslag_id)}
    except seo_ai.ForslagsFel as e:
        raise HTTPException(409, str(e))


@app.post("/api/seokarta/forslag/{forslag_id}/avvisa")
def seokarta_avvisa_forslag(forslag_id: int):
    seo_ai.avvisa(forslag_id)
    return {"ok": True}


# ---------- Saknade mål-URL ----------

class Sokvag(BaseModel):
    sokvag: str = Field(min_length=1, max_length=500)


class SkapaFranSaknad(Sokvag):
    huvudsokord: str = Field(min_length=2, max_length=120)
    sekundara_sokord: list[str] = Field(default_factory=list, max_length=MAX_SEKUNDARA)
    sokintention: str = Field(default="", max_length=40)
    kluster: str = Field(default="", max_length=120)
    bekraftad: bool = False


@app.get("/api/saknade")
def lista_saknade():
    return {"saknade": saknade.saknade()}


@app.post("/api/saknade/planera")
def planera_saknad(body: Sokvag):
    try:
        saknade.markera_planerad(body.sokvag)
    except saknade.SaknadFel as e:
        raise HTTPException(404, str(e))
    return {"ok": True}


@app.post("/api/saknade/ta-bort-planering")
def ta_bort_planering(body: Sokvag):
    saknade.ta_bort_planering(body.sokvag)
    return {"ok": True}


@app.post("/api/saknade/ai-forslag")
async def saknad_ai_forslag(body: Sokvag):
    try:
        return await saknade.ai_forslag(body.sokvag)
    except saknade.SaknadFel as e:
        raise HTTPException(409, str(e))


@app.post("/api/saknade/skapa-artikel", status_code=201)
def skapa_fran_saknad(body: SkapaFranSaknad):
    """Lägger en artikel i artikelkön som utkast. Kräver uttrycklig bekräftelse; genererar och publicerar inte."""
    if not body.bekraftad:
        raise HTTPException(400, "Bekräfta att artikeln ska läggas i artikelkön.")
    sokord = Sokord(huvudsokord=body.huvudsokord, sekundara_sokord=body.sekundara_sokord)
    try:
        artikel_id = saknade.skapa_artikel(body.sokvag, sokord.huvudsokord, sokord.sekundara_sokord,
                                           sokintention=body.sokintention, kluster=body.kluster)
    except saknade.SaknadFel as e:
        raise HTTPException(409, str(e))
    return {"artikel_id": artikel_id, "status": "utkast"}


# ---------- Länkförslag ----------

class Ids(BaseModel):
    ids: list[int] = Field(max_length=500)


class Bekraftelse(Ids):
    bekraftad: bool = False


class Ankare(BaseModel):
    ankartext: str = Field(min_length=1, max_length=200)


@app.get("/api/lankforslag")
def lista_lankforslag():
    return {"forslag": lankforslag.lista(("ny", "godkand", "konflikt", "fel", "simulerad", "verkstalld", "vantar_pa_publicering")),
            "andringar": lankforslag.andringar(), "logg": lankforslag.logg(), "lage": config.settings.publicering,
            "wordpress": config.settings.status()["wordpress"]}


@app.post("/api/lankforslag/berakna")
def berakna_lankforslag():
    """Förslag utan AI: stycken som redan nämner målsidans nyckelord. Ändrar ingenting på sajten."""
    return {"nya": lankforslag.berakna_for_sajten()}


@app.post("/api/lankforslag/ai")
async def ai_lankforslag():
    try:
        return {"nya": await lankforslag.ai_for_sajten()}
    except lankforslag.LankFel as e:
        raise HTTPException(409, str(e))


@app.get("/api/lankforslag/{fid}/forhandsgranskning")
def forhandsgranska_lank(fid: int):
    try:
        return lankforslag.forhandsgranska(fid)
    except lankforslag.LankFel as e:
        raise HTTPException(404, str(e))


@app.patch("/api/lankforslag/{fid}")
def redigera_lank(fid: int, body: Ankare):
    try:
        return lankforslag.redigera(fid, body.ankartext)
    except lankforslag.LankFel as e:
        raise HTTPException(409, str(e))


@app.post("/api/lankforslag/godkann")
def godkann_lankar(body: Ids):
    """Godkänner förslag. Godkännandet ändrar inte sajten; det gör först Uppdatera sajten."""
    return {"godkanda": lankforslag.byt_status(body.ids, ("ny", "konflikt", "fel"), "godkand")}


@app.post("/api/lankforslag/avvisa")
def avvisa_lankar(body: Ids):
    return {"avvisade": lankforslag.byt_status(body.ids, ("ny", "godkand", "konflikt", "fel", "vantar_pa_publicering"), "avvisad")}


@app.post("/api/lankforslag/aterkalla")
def aterkalla_lankar(body: Ids):
    return {"aterkallade": lankforslag.byt_status(body.ids, ("godkand",), "ny")}


@app.post("/api/lankforslag/verkstall")
async def verkstall_lankar(body: Bekraftelse):
    """Uppdaterar den publicerade sajten med godkända förslag. Kräver uttrycklig bekräftelse."""
    if not body.bekraftad:
        raise HTTPException(400, "Uppdateringen av sajten måste bekräftas.")
    try:
        return await lankforslag.verkstall(body.ids)
    except lankforslag.LankFel as e:
        raise HTTPException(409, str(e))


@app.post("/api/lankandringar/{andring_id}/aterstall")
async def aterstall_lankandring(andring_id: int):
    try:
        return {"resultat": await lankforslag.aterstall(andring_id)}
    except (lankforslag.LankFel, WordPressFel) as e:
        raise HTTPException(409, str(e))


# ---------- Gränssnitt ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
