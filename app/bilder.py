"""AI-bilder till artiklarna: motiv, generering, WebP, lagring och uppladdning till WordPress.

Regler som alltid gäller:
- Bilder genereras bara när du själv beställer dem och har sett kostnadsuppskattningen.
- En godkänd bild genereras aldrig om, varken automatiskt eller av misstag; lås upp den först.
- När artikeltexten genereras om behålls alla färdiga bilder.
- Varje bild sparas på servern (STUDIO_DATA/bilder) så fort den är klar, så att ett fel på en
  bild aldrig tar bort de andra. En misslyckad omgenerering behåller den tidigare bilden.
- Bilderna laddas upp till WordPress mediabibliotek först när artikeln skickas dit.

API-nyckeln för bildtjänsten läses bara på servern och skickas aldrig till webbläsaren eller loggarna.
"""

import asyncio
import base64
import hashlib
import io
import logging
import re
import shutil
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageOps

from . import config, db
from .demo import slugify
from .render import BILD_MARKOR
from .wordpress import WordPressFel

log = logging.getLogger("studio.bilder")

ANTAL = 4
STORLEK = "1536x1024"
MAX_BREDD = 1536
MIN_BREDD_UPPLADDNING = 600
WEBP_KVALITET = 80
MAX_UPPLADDNING = 15 * 1024 * 1024
MAX_PIXLAR = 40_000_000
MAX_TEXT = 1000
MAX_FORSOK = 3
VANTETID_BAS = 2.0
MAX_SAMTIDIGA = 2
# Bilder får bara ändras medan artikeln är ett utkast eller väntar på granskning.
BILD_STATUSAR = ("utkast", "granskas")
FILNAMN = re.compile(r"^[1-9]\d?-[0-9a-f]{12}\.webp$")

# Läggs alltid till sist i prompten, även när du skrivit motivet själv.
STIL = (
    "Style: photorealistic editorial photograph, bright and airy, soft natural daylight, Scandinavian setting "
    "(Swedish house or interior, light wood, white or pale walls, calm neutral colours, a few green plants where natural), "
    "realistic materials and proportions, sharp focus, landscape 3:2 composition. "
    "Strictly no text, letters, numbers, signs, labels, logos, brand names, watermarks or captions anywhere in the image. "
    "No recognisable faces in close-up."
)

_semafor = asyncio.Semaphore(MAX_SAMTIDIGA)
_uppgifter: dict[int, asyncio.Task] = {}

SCHEMA = """
CREATE TABLE IF NOT EXISTS bild_media (
    artikel_id   INTEGER NOT NULL,
    sha256       TEXT    NOT NULL,
    wp_media_id  INTEGER NOT NULL,
    skapad       TEXT    NOT NULL,
    PRIMARY KEY (artikel_id, sha256)
);
"""


class BildFel(Exception):
    """Fel med ett meddelande som kan visas för användaren."""

    def __init__(self, meddelande: str, kan_forsoka: bool = False):
        super().__init__(meddelande)
        self.kan_forsoka = kan_forsoka


# ---------- Bildtjänster ----------

class OpenAIBilder:
    """OpenAI:s bild-API, eller en tjänst med samma API via STUDIO_BILD_API_URL."""

    kalla = "ai"
    transport: httpx.AsyncBaseTransport | None = None  # för tester

    async def kontrollera(self) -> None:
        """Hämtar modellens uppgifter (gratis). Kastar BildFel om nyckeln eller modellen inte fungerar."""
        s = config.settings
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), transport=self.transport) as k:
                r = await k.get(f"{s.bild_api_url}/models/{s.bild_modell}",
                                headers={"Authorization": f"Bearer {s.openai_api_key}"})
        except httpx.TimeoutException:
            raise BildFel("Bildtjänsten svarade inte i tid.", kan_forsoka=True)
        except httpx.HTTPError:
            raise BildFel("Kunde inte nå bildtjänsten. Kontrollera internetanslutningen.", kan_forsoka=True)
        if r.status_code >= 400:
            raise _tolka_fel(r)

    async def generera(self, prompt: str) -> bytes:
        s = config.settings
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(s.bild_timeout, connect=15.0), transport=self.transport) as k:
                r = await k.post(
                    f"{s.bild_api_url}/images/generations",
                    headers={"Authorization": f"Bearer {s.openai_api_key}"},
                    json={"model": s.bild_modell, "prompt": prompt, "size": STORLEK, "quality": s.bild_kvalitet, "n": 1,
                          "output_format": "webp", "output_compression": 95},
                )
                if r.status_code >= 400:
                    raise _tolka_fel(r)
                try:
                    data = r.json()["data"][0]
                except (ValueError, KeyError, IndexError, TypeError):
                    raise BildFel("Svaret från bildtjänsten kunde inte tolkas.", kan_forsoka=True)
                if data.get("b64_json"):
                    return base64.b64decode(data["b64_json"])
                if data.get("url"):
                    bild = await k.get(data["url"])
                    if bild.status_code == 200:
                        return bild.content
                raise BildFel("Bildtjänsten skickade ingen bild.", kan_forsoka=True)
        except httpx.TimeoutException:
            raise BildFel("Bildtjänsten svarade inte i tid.", kan_forsoka=True)
        except httpx.HTTPError:
            raise BildFel("Kunde inte nå bildtjänsten. Kontrollera serverns internetanslutning.", kan_forsoka=True)


def _tolka_fel(r: httpx.Response) -> BildFel:
    """Begripligt felmeddelande. Tjänstens egen feltext visas inte; den kan innehålla promptdelar."""
    try:
        fel = r.json().get("error") or {}
    except (ValueError, AttributeError):
        fel = {}
    kod = str(fel.get("code") or fel.get("type") or "")
    modell = config.settings.bild_modell
    if r.status_code == 401:
        return BildFel("Bildtjänsten godkände inte API-nyckeln. Kontrollera OPENAI_API_KEY på servern.")
    if r.status_code == 403:
        return BildFel(f"Kontot saknar behörighet till bildmodellen {modell}. Leverantören kan kräva att organisationen verifieras.")
    if r.status_code == 404:
        return BildFel(f"Bildmodellen {modell} finns inte. Ändra STUDIO_BILD_MODELL.")
    if r.status_code == 429:
        if kod in ("insufficient_quota", "billing_hard_limit_reached"):
            return BildFel("Kontot hos bildtjänsten saknar krediter eller har nått sin kostnadsgräns.")
        return BildFel("Bildtjänsten tar emot för många anrop just nu.", kan_forsoka=True)
    if r.status_code == 400 and any(o in kod for o in ("moderation", "content_policy", "safety")):
        return BildFel("Bildtjänsten avböjde motivet. Ändra bildbeskrivningen och försök igen.")
    if r.status_code >= 500:
        return BildFel("Bildtjänsten är tillfälligt otillgänglig.", kan_forsoka=True)
    return BildFel(f"Bildtjänsten avvisade anropet (fel {r.status_code}{f', {kod}' if kod else ''}).")


class TestBilder:
    """Låtsasbilder som ritas lokalt. Kostar ingenting och kan inte användas i produktion."""

    kalla = "test"

    async def generera(self, prompt: str) -> bytes:
        h = hashlib.sha256(prompt.encode()).digest()
        bild = Image.new("RGB", (1536, 1024), (h[0] // 2 + 120, h[1] // 2 + 120, h[2] // 2 + 120))
        rita = ImageDraw.Draw(bild)
        rita.rectangle((0, 640, 1536, 1024), fill=(h[3] // 3 + 150, h[4] // 3 + 160, h[5] // 3 + 140))
        rita.ellipse((1100, 120, 1300, 320), fill=(250, 240, 210))
        buf = io.BytesIO()
        bild.save(buf, "PNG")
        return buf.getvalue()


def saknas_orsak() -> str | None:
    """Varför bildgenereringen inte är aktiv, eller None om den är det. Visar aldrig nyckeln."""
    s = config.settings
    if s.bildtjanst == "av":
        return "Bildgenereringen är avstängd (STUDIO_BILDTJANST=av)."
    if s.bildtjanst == "test":
        return None if not s.produktion else "Testbilder kan inte användas i produktion. Ta bort STUDIO_BILDTJANST=test."
    if not s.openai_api_key:
        if s.produktion:
            return "OPENAI_API_KEY saknas på servern. Lägg in den med ./scripts/fly_hemlighet.sh OPENAI_API_KEY (appen startar om)."
        return ("OPENAI_API_KEY saknas på servern. Lägg den i .env i projektmappen (eller spara den med "
                "python scripts/spara_hemlighet.py OPENAI_API_KEY) och starta om servern.")
    return None


def tjanst():
    if orsak := saknas_orsak():
        raise BildFel(orsak)
    return TestBilder() if config.settings.bildtjanst == "test" else OpenAIBilder()


async def kontrollera() -> dict:
    """Kontrollerar nyckel och modell utan att skapa någon bild. Kostar ingenting."""
    s = config.settings
    svar = {"tjanst": s.bildtjanst, "modell": s.bild_modell, "kvalitet": s.bild_kvalitet, "nyckel": bool(s.openai_api_key)}
    if orsak := saknas_orsak():
        return svar | {"ok": False, "meddelande": orsak}
    if s.bildtjanst == "test":
        return svar | {"ok": True, "meddelande": "Testbilder används (ritas lokalt, ingen kostnad). Ta bort STUDIO_BILDTJANST=test för riktiga AI-bilder."}
    try:
        await OpenAIBilder().kontrollera()
    except BildFel as e:
        return svar | {"ok": False, "meddelande": str(e)}
    return svar | {"ok": True, "meddelande": f"Nyckeln fungerar och modellen {s.bild_modell} är tillgänglig. Ingen bild skapades."}


def kostnad(antal: int) -> dict:
    """Uppskattad kostnad innan något beställs. Priset per bild är konfigurerbart."""
    s = config.settings
    gratis = s.bildtjanst == "test"
    per_bild = 0.0 if gratis else s.bild_pris_usd
    usd = round(antal * per_bild, 3)
    return {
        "antal": antal, "tjanst": s.bildtjanst, "modell": "testbilder" if gratis else s.bild_modell,
        "kvalitet": s.bild_kvalitet, "storlek": STORLEK.replace("x", "×"), "pris_per_bild_usd": per_bild,
        "total_usd": usd, "total_sek": round(usd * s.usd_sek, 2), "usd_sek": s.usd_sek,
    }


# ---------- Motiv, filnamn och alt-texter ----------

def _text(html_: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html_)).strip()


def plan(innehall: dict) -> dict[int, dict]:
    """Var varje bild står i artikeln: huvudbilden efter introduktionen, övriga vid sina avsnitt."""
    body = innehall.get("html", "")
    rubriker = [(m.start(), m.end(), _text(m.group(1))) for m in re.finditer(r"<h2[^>]*>(.*?)</h2>", body, re.S)]
    ut = {}
    for m in BILD_MARKOR.finditer(body):
        bid = int(m.group(1))
        fore = [r for r in rubriker if r[1] <= m.start()]
        efter = [r for r in rubriker if r[0] >= m.end()]
        if bid == 1 or not fore:
            # Huvudbilden: ämnet i stort, med introduktionen som sammanhang.
            nasta = efter[0][0] if efter else len(body)
            ut[bid] = {"roll": "huvudbild", "avsnitt": "Efter introduktionen", "kontext": _text(body[:nasta])[:500]}
            continue
        rubrik = fore[-1]
        slut = efter[0][0] if efter else len(body)
        ut[bid] = {"roll": "avsnitt", "avsnitt": rubrik[2], "kontext": _text(body[rubrik[1]:slut])[:500]}
    return ut


def bygg_prompt(bild: dict, innehall: dict) -> str:
    """Prompten till bildtjänsten: motivet för just den här bilden, artikelns sammanhang och Klimathemmets stil."""
    p = plan(innehall).get(bild["id"], {"roll": "avsnitt", "avsnitt": "", "kontext": ""})
    huvud = innehall.get("huvudsokord") or innehall.get("titel", "")
    motiv = (bild.get("prompt") or "").strip()[:2000]
    if not motiv:
        motiv = f"A realistic scene that illustrates: {p['avsnitt'] or huvud}. {p['kontext'][:300]}"
    if p["roll"] == "huvudbild":
        sammanhang = f"Hero image for a Swedish home-improvement guide titled “{innehall.get('titel', huvud)}” (topic: {huvud})."
    else:
        sammanhang = f"Image for the section “{p['avsnitt']}” in a Swedish home-improvement guide about {huvud}."
    return f"{motiv}\n\n{sammanhang}\n\n{STIL}"


def filnamn(innehall: dict) -> dict[int, str]:
    """Beskrivande filnamn (utan .webp) från alt-texten och huvudsökordet, unika inom artikeln."""
    nyckel = slugify(innehall.get("huvudsokord", "")) or "klimathemmet"
    ut: dict[int, str] = {}
    for b in innehall.get("bilder", []):
        namn = slugify(b.get("alt", ""))
        if nyckel not in namn:
            namn = f"{nyckel}-{namn}".strip("-")
        if len(namn) > 70:
            namn = namn[:70].rsplit("-", 1)[0]
        if not namn or namn in ut.values():
            namn = f"{namn or nyckel}-{b['id']}"
        ut[b["id"]] = namn
    return ut


def alt_kontroll(alt: str) -> str | None:
    """Varning om alt-texten, eller None om den ser bra ut."""
    alt = alt.strip()
    if len(alt) < 10:
        return "Alt-texten är för kort. Beskriv vad som syns på bilden."
    if len(alt) > 160:
        return "Alt-texten är lång. Håll den under cirka 125 tecken."
    if re.match(r"(?i)^(en )?(bild|foto|fotografi) (på|av)\b", alt):
        return "Börja inte med ”Bild på”; beskriv motivet direkt."
    return None


def bildinfo(innehall: dict | None) -> dict[int, dict]:
    """Extra uppgifter till Bild-fliken: placering, filnamn och alt-textkontroll."""
    if not innehall:
        return {}
    p, namn = plan(innehall), filnamn(innehall)
    return {
        b["id"]: {**p.get(b["id"], {"roll": "ej_placerad", "avsnitt": "Inte placerad i texten", "kontext": ""}),
                  "filnamn": f"{namn[b['id']]}.webp", "alt_varning": alt_kontroll(b.get("alt", ""))}
        for b in innehall.get("bilder", [])
    }


# ---------- Ändringar i innehållet ----------

def _hitta(innehall: dict, bild_id: int) -> dict | None:
    return next((b for b in innehall.get("bilder", []) if b.get("id") == bild_id), None)


def sla_ihop_klient(nytt: dict, server: dict | None) -> dict:
    """Webbläsaren får bara ändra alt-text, bildtext och motiv. Filer, status och godkännande ägs av servern."""
    klient = {b.get("id"): b for b in nytt.get("bilder", [])}
    bilder = []
    for sb in (server or {}).get("bilder", []):
        b = dict(sb)
        if kb := klient.get(sb["id"]):
            for falt in ("alt", "bildtext", "prompt"):
                b[falt] = str(kb.get(falt) or "")[:MAX_TEXT if falt != "prompt" else 2000]
        bilder.append(b)
    return {**nytt, "bilder": bilder}


def behall_vid_omgenerering(gamla: list[dict], nya: list[dict]) -> list[dict]:
    """Vid ny artikeltext behålls alla färdiga bilder; inga bilder genereras om automatiskt.

    En godkänd bild behålls helt. En ej godkänd bild behåller sin fil och alt-text men får det nya
    motivförslaget och markeras, så att du kan välja att generera om den.
    """
    gamla_id = {b["id"]: b for b in gamla}
    ut = []
    for nb in nya:
        gb = gamla_id.get(nb["id"])
        if not (gb and gb.get("fil")):
            ut.append(nb)
            continue
        b = dict(gb, status="klar", fel=None)
        if not gb.get("godkand"):
            b["prompt"] = nb.get("prompt") or gb.get("prompt", "")
            b["inaktuell"] = True
        ut.append(b)
    return ut


def markera_godkanda(innehall: dict) -> dict | None:
    """När artikeln godkänns räknas dess färdiga bilder som godkända."""
    andrad = False
    for b in innehall.get("bilder", []):
        if b.get("fil") and not b.get("godkand"):
            b["godkand"] = True
            andrad = True
    return innehall if andrad else None


def satt_godkand(artikel_id: int, bild_id: int, godkand: bool) -> dict | None:
    def andra(inn):
        b = _hitta(inn, bild_id)
        if b is None or (godkand and not b.get("fil")) or b.get("status") == "genereras":
            return None
        b["godkand"] = godkand
        return inn
    return db.andra_innehall(artikel_id, andra, BILD_STATUSAR)


# ---------- Lagring ----------

def mapp(artikel_id: int) -> Path:
    return config.settings.data_dir / "bilder" / str(int(artikel_id))


def sokvag(artikel_id: int, fil: str) -> Path | None:
    if not FILNAMN.match(fil):
        return None
    return mapp(artikel_id) / fil


def till_webp(data: bytes, min_bredd: int = 0) -> tuple[bytes, int, int]:
    """Optimerad WebP: högst 1536 px bred, utan metadata. Godtar JPEG, PNG och WebP."""
    Image.MAX_IMAGE_PIXELS = MAX_PIXLAR
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.format not in ("JPEG", "PNG", "WEBP"):
                raise BildFel("Filen måste vara en JPEG-, PNG- eller WebP-bild.")
            im.load()
            bild = ImageOps.exif_transpose(im)
    except BildFel:
        raise
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise BildFel("Bilden är för stor.")
    except Exception:
        raise BildFel("Filen kunde inte läsas som en bild.")
    if bild.width < min_bredd:
        raise BildFel(f"Bilden är för liten ({bild.width} px bred). Använd en bild som är minst {min_bredd} px bred.")
    if bild.mode in ("RGBA", "LA", "P"):
        bild = bild.convert("RGBA")
        bakgrund = Image.new("RGB", bild.size, (255, 255, 255))
        bakgrund.paste(bild, mask=bild.getchannel("A"))
        bild = bakgrund
    elif bild.mode != "RGB":
        bild = bild.convert("RGB")
    if bild.width > MAX_BREDD:
        bild = bild.resize((MAX_BREDD, round(bild.height * MAX_BREDD / bild.width)), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    bild.save(buf, "WEBP", quality=WEBP_KVALITET, method=6)
    return buf.getvalue(), bild.width, bild.height


def spara_bild(artikel_id: int, bild_id: int, data: bytes, kalla: str, min_bredd: int = 0) -> dict:
    """Konverterar och sparar bilden direkt, och kopplar den till artikeln i samma steg."""
    webp, bredd, hojd = till_webp(data, min_bredd)
    sha = hashlib.sha256(webp).hexdigest()
    fil = f"{bild_id}-{sha[:12]}.webp"
    katalog = mapp(artikel_id)
    katalog.mkdir(parents=True, exist_ok=True)
    tmp = katalog / f".{fil}.tmp"
    tmp.write_bytes(webp)
    tmp.replace(katalog / fil)
    gamla: list[str] = []

    def andra(inn):
        b = _hitta(inn, bild_id)
        if b is None:
            return None
        if b.get("fil") and b["fil"] != fil:
            gamla.append(b["fil"])
        b.update(fil=fil, url=f"/api/bilder/{artikel_id}/{fil}", sha256=sha, bredd=bredd, hojd=hojd,
                 storlek_kb=max(1, round(len(webp) / 1024)), kalla=kalla, status="klar", fel=None,
                 inaktuell=False, godkand=False, skapad=db.nu())
        return inn

    if db.andra_innehall(artikel_id, andra, BILD_STATUSAR) is None:
        (katalog / fil).unlink(missing_ok=True)
        raise BildFel("Artikeln kan inte ändras i sin nuvarande status, så bilden sparades inte.")
    for g in gamla:
        (katalog / g).unlink(missing_ok=True)
    return {"fil": fil, "bredd": bredd, "hojd": hojd}


def ta_bort_filer(artikel_id: int) -> None:
    shutil.rmtree(mapp(artikel_id), ignore_errors=True)


# ---------- Generering i bakgrunden ----------

def pagar(artikel_id: int) -> bool:
    task = _uppgifter.get(artikel_id)
    return bool(task and not task.done())


def starta(artikel_id: int, bild_ids: list[int]) -> list[int]:
    """Beställer bilder. Anropas bara efter att du bekräftat kostnaden."""
    tjanst()
    if pagar(artikel_id):
        raise BildFel("Bilder genereras redan för den här artikeln.")
    valda: list[int] = []
    stopp: list[str] = []

    def markera(inn):
        for b in inn.get("bilder", []):
            if b["id"] not in bild_ids:
                continue
            if b.get("godkand"):
                stopp.append(f"Bild {b['id']} är godkänd. Lås upp den först om du vill generera om den.")
                return None
            b["status"], b["fel"] = "genereras", None
            valda.append(b["id"])
        return inn if valda else None

    if db.andra_innehall(artikel_id, markera, BILD_STATUSAR) is None:
        raise BildFel(stopp[0] if stopp else "Bilder kan bara skapas för utkast och artiklar som väntar på granskning.")
    _uppgifter[artikel_id] = asyncio.create_task(_kor(artikel_id, valda))
    return valda


async def _kor(artikel_id: int, bild_ids: list[int]) -> None:
    try:
        artikel = db.hamta(artikel_id)
        innehall = artikel["innehall"] if artikel else None
        if not innehall:
            return
        bilder = [b for b in innehall.get("bilder", []) if b["id"] in bild_ids]
        await asyncio.gather(*(_en_bild(artikel_id, b, innehall) for b in bilder))
    finally:
        _uppgifter.pop(artikel_id, None)


async def _en_bild(artikel_id: int, bild: dict, innehall: dict) -> None:
    try:
        async with _semafor:
            t = tjanst()
            data = await _med_omforsok(t, bygg_prompt(bild, innehall))
            spara_bild(artikel_id, bild["id"], data, t.kalla)
        db.logga(artikel_id, "bild_genererad", f"Bild {bild['id']}")
    except BildFel as e:
        _misslyckad(artikel_id, bild["id"], str(e))
    except asyncio.CancelledError:
        _misslyckad(artikel_id, bild["id"], "Genereringen avbröts.")
        raise
    except Exception:
        log.exception("Oväntat fel vid bildgenerering för artikel %s, bild %s", artikel_id, bild["id"])
        _misslyckad(artikel_id, bild["id"], "Ett oväntat fel inträffade. Försök igen.")


async def _med_omforsok(t, prompt: str) -> bytes:
    """Tillfälliga fel och timeout försöks igen, med längre väntan för varje försök."""
    for forsok in range(1, MAX_FORSOK + 1):
        try:
            return await asyncio.wait_for(t.generera(prompt), timeout=config.settings.bild_timeout)
        except TimeoutError:
            fel = BildFel("Bildtjänsten svarade inte i tid.", kan_forsoka=True)
        except BildFel as e:
            fel = e
        if not fel.kan_forsoka or forsok == MAX_FORSOK:
            raise BildFel(f"{fel} Gav upp efter {forsok} försök." if forsok > 1 else str(fel))
        log.warning("Bildgenerering misslyckades (försök %s): %s", forsok, fel)
        await asyncio.sleep(VANTETID_BAS * 2 ** (forsok - 1))
    raise AssertionError("nås aldrig")


def _misslyckad(artikel_id: int, bild_id: int, meddelande: str) -> None:
    """Felet visas vid bilden. En tidigare färdig bild finns kvar."""
    def andra(inn):
        b = _hitta(inn, bild_id)
        if b is None:
            return None
        b["status"] = "klar" if b.get("fil") else "fel"
        b["fel"] = meddelande
        return inn
    db.andra_innehall(artikel_id, andra, db.STATUSAR)
    db.logga(artikel_id, "bild_misslyckad", f"Bild {bild_id}: {meddelande}")


def init() -> None:
    with db._conn() as c:
        c.executescript(SCHEMA)
        rader = c.execute("SELECT id FROM artiklar WHERE innehall LIKE '%\"genereras\"%'").fetchall()

    # Bilder som genererades när servern stängdes av kan inte återupptas.
    def aterstall(inn):
        andrad = False
        for b in inn.get("bilder", []):
            if b.get("status") == "genereras":
                b["status"] = "klar" if b.get("fil") else "fel"
                b["fel"] = "Genereringen avbröts när servern startades om. Försök igen."
                andrad = True
        return inn if andrad else None

    for (artikel_id,) in rader:
        db.andra_innehall(artikel_id, aterstall, db.STATUSAR)


# ---------- WordPress ----------

def _media_info(m: dict, bild: dict) -> dict:
    detaljer = m.get("media_details") or {}
    bredd = detaljer.get("width") or bild.get("bredd")
    hojd = detaljer.get("height") or bild.get("hojd")
    storlekar = []
    for s in (detaljer.get("sizes") or {}).values():
        w, h, url = s.get("width"), s.get("height"), s.get("source_url")
        # Bara storlekar med samma proportioner; WordPress beskurna miniatyrer passar inte i srcset.
        if url and w and h and bredd and hojd and abs(w / h - bredd / hojd) < 0.02:
            storlekar.append({"url": url, "bredd": w})
    return {"id": m["id"], "url": m.get("source_url"), "bredd": bredd, "hojd": hojd,
            "storlekar": sorted({s["bredd"]: s for s in storlekar}.values(), key=lambda s: s["bredd"])}


async def ladda_upp_till_wordpress(wp, artikel: dict) -> dict[int, dict]:
    """Laddar upp artikelns färdiga bilder till mediabiblioteket. En bild laddas aldrig upp två gånger."""
    innehall = artikel["innehall"]
    placerade = {int(x) for x in BILD_MARKOR.findall(innehall.get("html", ""))}
    namn = filnamn(innehall)
    media: dict[int, dict] = {}
    for b in innehall.get("bilder", []):
        if b["id"] not in placerade or not b.get("fil"):
            continue
        if b.get("kalla") == "test":
            raise WordPressFel("Artikeln har testbilder (STUDIO_BILDTJANST=test). Skapa riktiga bilder innan den skickas till WordPress.")
        fil = sokvag(artikel["id"], b["fil"])
        if fil is None or not fil.is_file():
            raise WordPressFel(f"Bildfilen för bild {b['id']} saknas på servern. Skapa bilden igen.")
        with db._conn() as c:
            rad = c.execute("SELECT wp_media_id FROM bild_media WHERE artikel_id = ? AND sha256 = ?",
                            (artikel["id"], b["sha256"])).fetchone()
        m = await wp.hamta_media(rad[0]) if rad else None
        if m is None:
            m = await wp.ladda_upp_media(fil.read_bytes(), f"{namn[b['id']]}.webp", b.get("alt", ""), b.get("alt", "")[:100])
            with db._conn() as c:
                c.execute("INSERT OR REPLACE INTO bild_media (artikel_id, sha256, wp_media_id, skapad) VALUES (?, ?, ?, ?)",
                          (artikel["id"], b["sha256"], m["id"], db.nu()))
            db.logga(artikel["id"], "bild_uppladdad", f"Bild {b['id']} → mediabiblioteket (id {m['id']})")
        elif (m.get("alt_text") or "") != b.get("alt", ""):
            m = await wp.uppdatera_media(m["id"], {"alt_text": b.get("alt", "")})
        media[b["id"]] = _media_info(m, b)
    return media
