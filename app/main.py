"""Klimathemmet AI Studio – webbserver.

Starta: uvicorn app.main:app --reload
"""

import base64
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import db, jobb
from .config import settings
from .planering import MAX_SEKUNDARA, CsvFel, liknande, normalisera_sokord, tolka_csv
from .render import wordpress_block

STATIC = Path(__file__).resolve().parent / "static"
MAX_CSV_BYTES = 2 * 1024 * 1024


@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init()
    yield


app = FastAPI(title="Klimathemmet AI Studio", docs_url=None, redoc_url=None, lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Skyddar hela studion med lösenord när STUDIO_USER/STUDIO_PASSWORD är satta."""
    if settings.studio_user and settings.studio_password:
        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, settings.studio_user) and secrets.compare_digest(
                    pwd, settings.studio_password
                )
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="AI Studio"'})
    return await call_next(request)


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
    return settings.status()


@app.post("/api/exportera")
def exportera(article: Article):
    return {"kod": wordpress_block(article.model_dump())}


@app.post("/api/wordpress")
def skicka_till_wordpress(article: Article):
    raise HTTPException(status_code=501, detail="WordPress-integrationen byggs i ett senare steg.")


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
    return artikel


LASTA = ("genereras", "schemalagd", "publicerad")


@app.patch("/api/artiklar/{artikel_id}/sokord")
def andra_sokord(artikel_id: int, sokord: Sokord):
    artikel = _hamta_eller_404(artikel_id)
    if artikel["status"] in LASTA:
        raise HTTPException(409, "Sökorden kan inte ändras medan artikeln genereras, är schemalagd eller publicerad.")
    db.uppdatera(artikel_id, huvudsokord=sokord.huvudsokord, sekundara=sokord.sekundara_sokord)
    return db.hamta(artikel_id)


@app.put("/api/artiklar/{artikel_id}/innehall")
def spara_innehall(artikel_id: int, article: Article):
    artikel = _hamta_eller_404(artikel_id)
    if artikel["status"] not in ("utkast", "granskas", "godkand") or not artikel["har_innehall"]:
        raise HTTPException(409, "Artikeln kan inte redigeras i sin nuvarande status.")
    innehall = article.model_dump()
    innehall["huvudsokord"] = artikel["huvudsokord"]
    innehall["sekundara_sokord"] = artikel["sekundara_sokord"]
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


@app.post("/api/artiklar/{artikel_id}/godkann")
def godkann(artikel_id: int):
    artikel = _hamta_eller_404(artikel_id)
    return _byt(artikel_id, ("granskas",), "godkand", "godkand", None,
                "Bara artiklar som väntar på granskning kan godkännas.",
                godkand_at=db.nu(), godkand_hash=db.innehall_hash(artikel["innehall"]))


@app.post("/api/artiklar/{artikel_id}/begar-andringar")
def begar_andringar(artikel_id: int, body: Kommentar):
    kommentar = body.kommentar.strip()
    if not kommentar:
        raise HTTPException(400, "Beskriv vilka ändringar du vill ha.")
    return _byt(artikel_id, ("granskas", "godkand"), "utkast", "andringar_begarda", kommentar,
                "Ändringar kan bara begäras för artiklar som väntar på granskning eller är godkända.",
                andringar=kommentar, godkand_at=None, godkand_hash=None)


@app.post("/api/artiklar/{artikel_id}/avvisa")
def avvisa(artikel_id: int, body: Kommentar):
    return _byt(artikel_id, ("utkast", "granskas", "godkand", "fel"), "avvisad", "avvisad", body.kommentar.strip(),
                "Artikeln kan inte avvisas i sin nuvarande status.", godkand_at=None, godkand_hash=None)


@app.post("/api/artiklar/{artikel_id}/aterstall")
def aterstall(artikel_id: int):
    return _byt(artikel_id, ("avvisad",), "utkast", "aterstalld", None, "Bara avvisade artiklar kan återställas.")


@app.post("/api/artiklar/{artikel_id}/aterkalla")
def aterkalla(artikel_id: int):
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


# ---------- Gränssnitt ----------

@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
