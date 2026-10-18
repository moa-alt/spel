"""Länkförslag mellan sidor på sajten, med godkännande, förhandsgranskning och återställning.

Säkerhetsregler:
- Inget skrivs till den publicerade sajten förrän du har godkänt förslaget OCH uttryckligen valt
  att uppdatera sajten. Det sker bara i publiceringsläget "skarp"; i andra lägen simuleras ändringen.
- Före varje ändring hämtas artikeln från WordPress. Har den ändrats sedan kartläggningen stoppas
  ändringen (konflikt), så att manuella redigeringar aldrig skrivs över.
- Bara ankartexten i det angivna stycket görs till en länk; resten av innehållet lämnas orört.
- Varje ändring sparas med före- och efterversion och kan återställas.
"""

import asyncio
import html
import json
import logging
import re

import anthropic

from . import config, db, sajtkarta
from .planering import _token, likhet
from .wordpress import WordPress, WordPressFel

log = logging.getLogger("studio.lankforslag")

SCHEMA = """
CREATE TABLE IF NOT EXISTS lankforslag (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    kalla_id       INTEGER NOT NULL,           -- sajt_sidor.id för sidan där länken placeras
    mal_id         INTEGER,                    -- sajt_sidor.id för målsidan (om den finns på sajten)
    mal_artikel_id INTEGER,                    -- artikel i studion som ännu inte är publicerad
    mal_url        TEXT NOT NULL,
    ankartext      TEXT NOT NULL,
    stycke         TEXT NOT NULL,
    motivering     TEXT NOT NULL DEFAULT '',
    ursprung       TEXT NOT NULL,              -- beraknat, ai
    status         TEXT NOT NULL,              -- vantar_pa_publicering, ny, godkand, avvisad, verkstalld, simulerad, konflikt, fel, aterstalld
    fel            TEXT,
    skapad         TEXT NOT NULL,
    uppdaterad     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS lankforslag_status ON lankforslag(status);
CREATE TABLE IF NOT EXISTS lankandringar (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    kalla_id     INTEGER NOT NULL,
    wp_id        INTEGER NOT NULL,
    typ          TEXT NOT NULL,
    forslag_ids  TEXT NOT NULL,
    fore         TEXT NOT NULL,
    efter        TEXT NOT NULL,
    efter_andrad TEXT,
    status       TEXT NOT NULL,                -- verkstalld, aterstalld
    tid          TEXT NOT NULL
);
"""

AKTIVA = ("vantar_pa_publicering", "ny", "godkand")
GENERISKA_ANKARE = {"här", "klicka här", "läs mer", "länk", "denna sida", "den här sidan", "mer info", "mer information", "läs här"}
MAX_ANKARORD = 8
MAX_SAMMA_ANKARE = 2


class LankFel(Exception):
    pass


def init() -> None:
    with db._conn() as c:
        c.executescript(SCHEMA)


# ---------- Hjälpfunktioner ----------

def _sida(c, sid: int) -> dict | None:
    r = c.execute("SELECT * FROM sajt_sidor WHERE id = ?", (sid,)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["stycken"] = json.loads(d.get("stycken") or "[]")
    return d


def _bas() -> str:
    return config.settings.wp_url.rstrip("/") + "/"


def _sokvag(url: str) -> str | None:
    return sajtkarta.normalisera(url, _bas()) if config.settings.wp_url else None


def huvudnyckelord(sida: dict) -> tuple[str, str]:
    """Sidans huvudnyckelord och varifrån det kommer: studion, SEO-pluginet eller en uppskattning från titeln."""
    if sida.get("typ") == "post" and sida.get("wp_id"):
        with db._conn() as c:
            r = c.execute("SELECT huvudsokord FROM artiklar WHERE wp_post_id = ?", (sida["wp_id"],)).fetchone()
        if r:
            return r[0], "AI Studio"
    if sida.get("fokus"):
        return sida["fokus"], "SEO-plugin"
    titel = re.split(r"\s[–—:|-]\s|:\s|\?", sida.get("titel") or "")[0].strip()
    return titel.lower(), "uppskattat från titeln"


def validera(kalla: dict, mal_url: str, ankartext: str, stycke: str, mal_id: int | None = None) -> str | None:
    """Returnerar ett felmeddelande om förslaget inte håller, annars None."""
    if kalla["typ"] not in ("post", "page"):
        return "Länkar kan bara placeras i artiklar och sidor."
    if kalla["url"] == mal_url:
        return "En sida kan inte länka till sig själv."
    if stycke not in kalla["stycken"]:
        return "Stycket finns inte i källartikeln."
    ankartext = ankartext.strip()
    if not ankartext or ankartext not in stycke:
        return "Ankartexten finns inte ordagrant i stycket."
    if ankartext == stycke or len(ankartext.split()) > MAX_ANKARORD:
        return "Ankartexten är för lång."
    if ankartext.lower() in GENERISKA_ANKARE:
        return "Ankartexten är för allmän."
    malvag = _sokvag(mal_url)
    with db._conn() as c:
        if c.execute("SELECT 1 FROM sajt_lankar WHERE fran_id = ? AND (till_url = ? OR (till_id IS NOT NULL AND till_id = ?))",
                     (kalla["id"], malvag, mal_id)).fetchone():
            return "Källartikeln länkar redan till målsidan."
        # Texten får inte redan ingå i en länk i källartikeln.
        for (befintlig,) in c.execute("SELECT ankartext FROM sajt_lankar WHERE fran_id = ?", (kalla["id"],)):
            if ankartext.lower() in (befintlig or "").lower():
                return "Ankartexten ingår redan i en befintlig länk i artikeln."
        platser = ",".join("?" for _ in AKTIVA)
        if c.execute(f"SELECT 1 FROM lankforslag WHERE kalla_id = ? AND mal_url = ? AND status IN ({platser})",
                     (kalla["id"], mal_url, *AKTIVA)).fetchone():
            return "Det finns redan ett förslag på samma länk."
        # Undvik överoptimering: samma ankartext till samma mål får förekomma högst ett par gånger.
        anvanda = c.execute("SELECT COUNT(*) FROM sajt_lankar WHERE till_url = ? AND lower(ankartext) = ?",
                            (malvag, ankartext.lower())).fetchone()[0]
        anvanda += c.execute(f"SELECT COUNT(*) FROM lankforslag WHERE mal_url = ? AND lower(ankartext) = ? AND status IN ({platser})",
                             (mal_url, ankartext.lower(), *AKTIVA)).fetchone()[0]
    if anvanda >= MAX_SAMMA_ANKARE:
        return "Ankartexten används redan för målsidan; variera den."
    return None


def _spara(kalla: dict, mal_url: str, ankartext: str, stycke: str, motivering: str, ursprung: str,
           mal_id: int | None = None, mal_artikel_id: int | None = None) -> int | None:
    if validera(kalla, mal_url, ankartext, stycke, mal_id):
        return None
    status = "vantar_pa_publicering" if mal_artikel_id and not mal_id else "ny"
    with db._conn() as c:
        return c.execute(
            "INSERT INTO lankforslag (kalla_id, mal_id, mal_artikel_id, mal_url, ankartext, stycke, motivering, ursprung, status, "
            "skapad, uppdaterad) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (kalla["id"], mal_id, mal_artikel_id, mal_url, ankartext.strip(), stycke, motivering.strip(), ursprung, status, db.nu(), db.nu()),
        ).lastrowid


# ---------- Beräknade förslag (utan AI) ----------

def _hitta_fras(stycke: str, fras: str) -> str | None:
    """Frasen som den står i stycket (samma skiftläge), om den finns som hela ord."""
    m = re.search(rf"(?<![\w-]){re.escape(fras)}(?![\w-])", stycke, re.I)
    return m.group(0) if m else None


def berakna_for_mal(mal_url: str, fraser: list[str], mal_id: int | None = None, mal_artikel_id: int | None = None,
                    kluster_text: str = "", max_antal: int = 5) -> list[int]:
    """Hittar stycken på sajten som redan nämner målsidans nyckelord och föreslår en länk där."""
    fraser = [f.strip() for f in dict.fromkeys(fraser) if f and len(f.strip()) >= 4]
    if not fraser:
        return []
    mal_token = _token(kluster_text or " ".join(fraser))
    with db._conn() as c:
        kallor = [_sida(c, r[0]) for r in c.execute("SELECT id FROM sajt_sidor WHERE typ IN ('post', 'page')")]
    # Mest närliggande sidor först.
    kallor.sort(key=lambda k: -len(mal_token & _token(k["titel"] + " " + " ".join(k["stycken"][:3]))))
    ids = []
    for kalla in kallor:
        if len(ids) >= max_antal:
            break
        if kalla["url"] == mal_url:
            continue
        for stycke in kalla["stycken"]:
            traff = next((t for t in (_hitta_fras(stycke, f) for f in fraser) if t), None)
            if traff:
                fid = _spara(kalla, mal_url, traff, stycke, f"Stycket nämner ”{traff}”, som är målsidans ämne.",
                             "beraknat", mal_id, mal_artikel_id)
                if fid:
                    ids.append(fid)
                    break
    return ids


def karnfras(fras: str) -> str:
    """Kärnan i en nyckelordsfras: "FTX-ventilation i villa" → "FTX-ventilation". Tom om den blir för allmän."""
    karna = re.split(r"\s(?:i|för|på|till|med|utan|vid|och)\s", fras, maxsplit=1)[0].strip()
    # Ett ensamt allmänt ord ("ventilation") säger för lite; sammansatta begrepp ("FTX-ventilation") duger.
    return karna if karna != fras and (len(karna.split()) >= 2 or re.search(r"[-\d]", karna)) else ""


def berakna_for_sajten(max_mal: int = 15) -> int:
    """Förslag till de sidor som behöver länkar mest: isolerade, få inkommande, pelarsidor och kommersiella sidor."""
    analys = sajtkarta.analysera()
    noder = [n for n in analys["noder"] if n["typ"] in ("post", "page")]
    noder.sort(key=lambda n: (not n["kommersiell"], n["in"] > 0, n["roll"] != "pelare", n["in"]))
    antal = 0
    # Läs sidorna först och släpp databaslåset innan förslagen skapas (låset är inte återinträdbart).
    with db._conn() as c:
        sidor = [_sida(c, n["id"]) for n in noder[:max_mal]]
    for sida in sidor:
        if sida:
            sida_fras, _ = huvudnyckelord(sida)
            antal += len(berakna_for_mal(sida["url"], [sida_fras, karnfras(sida_fras)], mal_id=sida["id"],
                                         kluster_text=sida["titel"], max_antal=3))
    return antal


def for_ny_artikel(artikel: dict) -> int:
    """Föreslår befintliga artiklar som kan länka till en ny artikel i studion (väntar tills den är publicerad)."""
    innehall = artikel.get("innehall") or {}
    if not innehall.get("slug") or not config.settings.wp_url:
        return 0
    with db._conn() as c:
        platser = ",".join("?" for _ in AKTIVA)
        c.execute(f"DELETE FROM lankforslag WHERE mal_artikel_id = ? AND status IN ({platser})", (artikel["id"], *AKTIVA))
    mal_url = f"{config.settings.wp_url.rstrip('/')}/{innehall['slug']}/"
    fraser = [artikel["huvudsokord"], *artikel.get("sekundara_sokord", [])]
    return len(berakna_for_mal(mal_url, fraser, mal_artikel_id=artikel["id"], kluster_text=" ".join(fraser), max_antal=6))


def artikel_publicerad(artikel_id: int, lank: str) -> None:
    """När en ny artikel publicerats blir dess väntande förslag möjliga att godkänna."""
    with db._conn() as c:
        c.execute("UPDATE lankforslag SET mal_url = ?, status = 'ny', uppdaterad = ? WHERE mal_artikel_id = ? "
                  "AND status = 'vantar_pa_publicering'", (lank, db.nu(), artikel_id))
        c.execute("INSERT INTO installningar (nyckel, varde) VALUES ('kartlagg_behovs', '1') "
                  "ON CONFLICT(nyckel) DO UPDATE SET varde = '1'")


# ---------- AI-förslag ----------

AI_SCHEMA = {
    "type": "object",
    "properties": {"forslag": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "mal_url": {"type": "string"},
            "stycke_nr": {"type": "integer"},
            "ankartext": {"type": "string"},
            "motivering": {"type": "string"},
        },
        "required": ["mal_url", "stycke_nr", "ankartext", "motivering"],
        "additionalProperties": False,
    }}},
    "required": ["forslag"],
    "additionalProperties": False,
}

AI_SYSTEM = """Du föreslår interna länkar på Klimathemmet (klimathemmet.se), en svensk sajt om inomhusklimat.

Du får en källartikel uppdelad i numrerade stycken och en lista med möjliga målsidor.
Föreslå bara länkar som verkligen hjälper läsaren att gå vidare, och prioritera relevans framför antal.

Regler:
- Högst 3 förslag, gärna färre. Inga förslag är bättre än svaga förslag.
- ankartext måste vara ett sammanhängande utdrag som står ORDAGRANT i det angivna stycket (samma stavning och skiftläge), 2–6 ord.
- Ankartexten ska vara naturlig och beskrivande, inte en exakt sökordsfras som tryckts in och aldrig ”klicka här” eller ”läs mer”.
- Länka inte i rubriker, och högst en länk per målsida.
- Använd bara mål-URL:er exakt som de står i listan.
- motivering: en mening om varför länken hjälper läsaren just där."""


async def _ai_for_kalla(kalla: dict, mal: list[dict], client) -> list[int]:
    stycken = "\n".join(f"[{i}] {t}" for i, t in enumerate(kalla["stycken"][:40]))
    mallista = "\n".join(f"- {m['titel']} ({m['nyckelord']}): {m['url']}" for m in mal)
    async with client.messages.stream(
        model=config.settings.claude_model, max_tokens=16000, thinking={"type": "adaptive"},
        output_config={"effort": config.settings.claude_effort, "format": {"type": "json_schema", "schema": AI_SCHEMA}},
        system=AI_SYSTEM,
        messages=[{"role": "user", "content": f"Källartikel: {kalla['titel']}\n\nStycken:\n{stycken}\n\nMöjliga målsidor:\n{mallista}"}],
        extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
    ) as stream:
        message = await stream.get_final_message()
    if message.stop_reason in ("refusal", "max_tokens"):
        return []
    blocks = list(message.content)
    sista = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    try:
        data = json.loads("".join(b.text for b in blocks[sista + 1:] if b.type == "text"))
    except json.JSONDecodeError:
        return []
    tillatna = {m["url"]: m for m in mal}
    ids = []
    for f in data.get("forslag", [])[:3]:
        nr = f.get("stycke_nr")
        if f.get("mal_url") not in tillatna or not isinstance(nr, int) or not 0 <= nr < len(kalla["stycken"]):
            continue
        fid = _spara(kalla, f["mal_url"], f.get("ankartext", ""), kalla["stycken"][nr], f.get("motivering", ""), "ai",
                     mal_id=tillatna[f["mal_url"]]["id"])
        if fid:
            ids.append(fid)
    return ids


async def ai_for_sajten(max_kallor: int = 6) -> int:
    """Låter AI läsa de källartiklar som har flest relevanta mål utan länk och föreslå kontextuella länkar."""
    if not config.settings.anthropic_api_key:
        raise LankFel("AI-förslag kräver ANTHROPIC_API_KEY.")
    analys = sajtkarta.analysera()
    noder = {n["id"]: n for n in analys["noder"]}
    with db._conn() as c:
        sidor = {r[0]: _sida(c, r[0]) for r in c.execute("SELECT id FROM sajt_sidor WHERE typ IN ('post', 'page')")}
        lankar = {(r[0], r[1]) for r in c.execute("SELECT fran_id, till_id FROM sajt_lankar WHERE till_id IS NOT NULL")}
    # Mål som behöver länkar mest, och för varje källa de närliggande målen som saknar länk.
    prio = sorted(sidor.values(), key=lambda s: (not noder[s["id"]]["kommersiell"], noder[s["id"]]["in"] > 2,
                                                  noder[s["id"]]["roll"] != "pelare", noder[s["id"]]["in"]))
    kandidater: dict[int, list[dict]] = {}
    for mal in prio[:30]:
        nyckelord, _ = huvudnyckelord(mal)
        for kalla in sidor.values():
            if kalla["id"] == mal["id"] or (kalla["id"], mal["id"]) in lankar or not kalla["stycken"]:
                continue
            if noder[kalla["id"]]["kluster"] == noder[mal["id"]]["kluster"] or likhet(kalla["titel"], mal["titel"]) >= 0.2:
                kandidater.setdefault(kalla["id"], []).append({"id": mal["id"], "url": mal["url"], "titel": mal["titel"], "nyckelord": nyckelord})
    kallor = sorted(kandidater.items(), key=lambda x: -len(x[1]))[:max_kallor]
    client = anthropic.AsyncAnthropic(api_key=config.settings.anthropic_api_key, max_retries=3)
    sem = asyncio.Semaphore(2)

    async def en(kid, mal):
        async with sem:
            try:
                return await _ai_for_kalla(sidor[kid], mal[:8], client)
            except anthropic.APIError as e:
                log.warning("AI-länkförslag misslyckades för sida %s: %s", kid, type(e).__name__)
                return []

    resultat = await asyncio.gather(*(en(k, m) for k, m in kallor))
    return sum(len(r) for r in resultat)


# ---------- Lista, godkänna, avvisa ----------

def lista(status: tuple[str, ...] | None = None) -> list[dict]:
    with db._conn() as c:
        sql = ("SELECT f.*, k.titel AS kalla_titel, k.url AS kalla_url, m.titel AS mal_titel, a.huvudsokord AS mal_artikel "
               "FROM lankforslag f JOIN sajt_sidor k ON k.id = f.kalla_id LEFT JOIN sajt_sidor m ON m.id = f.mal_id "
               "LEFT JOIN artiklar a ON a.id = f.mal_artikel_id")
        if status:
            sql += f" WHERE f.status IN ({','.join('?' for _ in status)})"
        rows = c.execute(sql + " ORDER BY f.id DESC", status or ()).fetchall()
    return [dict(r) for r in rows]


def for_sida(sid: int) -> list[dict]:
    return [f for f in lista(AKTIVA) if sid in (f["kalla_id"], f["mal_id"])]


def for_artikel(artikel_id: int) -> list[dict]:
    return [f for f in lista() if f["mal_artikel_id"] == artikel_id]


def byt_status(ids: list[int], fran: tuple[str, ...], till: str) -> int:
    if not ids:
        return 0
    with db._conn() as c:
        cur = c.execute(
            f"UPDATE lankforslag SET status = ?, fel = NULL, uppdaterad = ? WHERE id IN ({','.join('?' for _ in ids)}) "
            f"AND status IN ({','.join('?' for _ in fran)})", (till, db.nu(), *ids, *fran))
        return cur.rowcount


def redigera(fid: int, ankartext: str) -> dict:
    with db._conn() as c:
        f = c.execute("SELECT * FROM lankforslag WHERE id = ?", (fid,)).fetchone()
        if not f or f["status"] not in ("ny", "vantar_pa_publicering"):
            raise LankFel("Bara nya förslag kan redigeras.")
        kalla = _sida(c, f["kalla_id"])
    ankartext = " ".join(ankartext.split())
    # Validera utan att räkna förslaget självt som en dubblett.
    with db._conn() as c:
        c.execute("UPDATE lankforslag SET status = 'redigeras' WHERE id = ?", (fid,))
    fel = validera(kalla, f["mal_url"], ankartext, f["stycke"], f["mal_id"])
    with db._conn() as c:
        if fel:
            c.execute("UPDATE lankforslag SET status = ? WHERE id = ?", (f["status"], fid))
        else:
            c.execute("UPDATE lankforslag SET ankartext = ?, status = ?, uppdaterad = ? WHERE id = ?",
                      (ankartext, f["status"], db.nu(), fid))
    if fel:
        raise LankFel(fel)
    return {"ankartext": ankartext}


# ---------- Infoga länk i WordPress-innehåll ----------

_TAGG = re.compile(r"(<[^>]+>)")
_RUBRIK = re.compile(r"^h[1-6]$")


def _tagnamn(tagg: str) -> tuple[str, bool]:
    m = re.match(r"<\s*(/)?\s*([a-zA-Z0-9]+)", tagg)
    return (m.group(2).lower(), bool(m.group(1))) if m else ("", False)


def _norm(text: str) -> str:
    return " ".join(html.unescape(text).replace("\xa0", " ").split())


def _hela_ord(text: str, fras: str) -> int:
    """Position för frasen som hela ord (inte inuti t.ex. "FTX-ventilation"), annars -1."""
    m = re.search(rf"(?<![\w-]){re.escape(fras)}(?![\w-])", text)
    return m.start() if m else -1


def infoga_lank(raw: str, ankartext: str, stycke: str, url: str) -> str:
    """Gör ankartexten i det angivna stycket till en länk. Allt annat i innehållet lämnas exakt som det var."""
    malvag = _sokvag(url)
    for m in re.finditer(r'href="([^"]*)"', raw):
        if _sokvag(html.unescape(m.group(1))) == malvag:
            raise LankFel("Artikeln länkar redan till målsidan.")
    delar = _TAGG.split(raw)
    # Första passet: hitta blocken (p/li) och deras text.
    block: list[dict] = []
    aktuellt = None
    for i, d in enumerate(delar):
        if d.startswith("<"):
            namn, slut = _tagnamn(d)
            if namn in ("p", "li"):
                if not slut and aktuellt is None:
                    aktuellt = {"start": i, "text": []}
                elif slut and aktuellt is not None:
                    aktuellt["slut"] = i
                    block.append(aktuellt)
                    aktuellt = None
        elif aktuellt is not None:
            aktuellt["text"].append(d)
    mal_text = _norm(stycke)
    traffar = [b for b in block if _norm("".join(b["text"])) == mal_text]
    if len(traffar) != 1:
        raise LankFel("Stycket hittades inte i artikeln. Den kan ha ändrats; kartlägg om sajten.")
    b = traffar[0]
    # Andra passet: ankartexten som sammanhängande text, utanför befintliga länkar och rubriker.
    inne_i = 0
    varianter = list(dict.fromkeys([ankartext, html.escape(ankartext, quote=False)]))
    for i in range(b["start"] + 1, b["slut"]):
        d = delar[i]
        if d.startswith("<"):
            namn, slut = _tagnamn(d)
            if namn == "a" or _RUBRIK.match(namn):
                inne_i += -1 if slut else 1
            continue
        if inne_i > 0:
            continue
        for v in varianter:
            pos = _hela_ord(d, v)
            if pos >= 0:
                lank = f'<a href="{html.escape(url, quote=True)}">{d[pos:pos + len(v)]}</a>'
                delar[i] = d[:pos] + lank + d[pos + len(v):]
                return "".join(delar)
    raise LankFel("Ankartexten hittades inte som sammanhängande text i stycket.")


def ta_bort_lank(raw: str, url: str, ankartext: str) -> str | None:
    """Tar bort exakt den länk som studion lade till. None om den inte längre finns."""
    for v in dict.fromkeys([ankartext, html.escape(ankartext, quote=False)]):
        lank = f'<a href="{html.escape(url, quote=True)}">{v}</a>'
        if lank in raw:
            return raw.replace(lank, v, 1)
    return None


def forhandsgranska(fid: int) -> dict:
    with db._conn() as c:
        f = c.execute("SELECT * FROM lankforslag WHERE id = ?", (fid,)).fetchone()
    if not f:
        raise LankFel("Förslaget finns inte.")
    i = f["stycke"].find(f["ankartext"])
    return {"fore": f["stycke"], "fore_del": [f["stycke"][:i], f["ankartext"], f["stycke"][i + len(f["ankartext"]):]],
            "url": f["mal_url"]}


# ---------- Uppdatera sajten ----------

async def verkstall(ids: list[int], transport=None) -> dict:
    """Uppdaterar sajten med godkända förslag. Skriver bara i publiceringsläget "skarp"."""
    skarp = config.settings.publicering == "skarp"
    platser = ",".join("?" for _ in ids)
    with db._conn() as c:
        rader = [dict(r) for r in c.execute(
            f"SELECT * FROM lankforslag WHERE id IN ({platser}) AND status = 'godkand'", ids)] if ids else []
        kallor = {r["kalla_id"]: _sida(c, r["kalla_id"]) for r in rader}
    resultat = {"verkstallda": 0, "simulerade": 0, "konflikter": 0, "fel": 0}
    if not rader:
        return resultat

    def satt(fids, status, fel=None):
        with db._conn() as c:
            c.execute(f"UPDATE lankforslag SET status = ?, fel = ?, uppdaterad = ? WHERE id IN ({','.join('?' for _ in fids)})",
                      (status, fel, db.nu(), *fids))

    wp_ok = bool(config.settings.wp_url and config.settings.wp_user and config.settings.wp_app_password)
    if skarp and not wp_ok:
        raise LankFel("WordPress-uppgifterna saknas på servern; sajten kan inte uppdateras.")
    if not wp_ok:
        # Ingen anslutning: simulera mot det kartlagda stycket.
        for r in rader:
            satt([r["id"]], "simulerad", "Simulering: ingenting ändrades på sajten.")
            db.logga(0, "lank_simulerad", f"{kallor[r['kalla_id']]['titel']} → {r['mal_url']} (”{r['ankartext']}”)")
        resultat["simulerade"] = len(rader)
        return resultat

    async with WordPress(transport=transport) as wp:
        for kid, grupp in _gruppera(rader).items():
            kalla = kallor[kid]
            fids = [r["id"] for r in grupp]
            try:
                post = await wp.hamta_redigerbar(kalla["typ"], kalla["wp_id"])
            except WordPressFel as e:
                satt(fids, "fel", str(e))
                resultat["fel"] += len(fids)
                continue
            if kalla["andrad"] and post.get("modified_gmt") != kalla["andrad"]:
                satt(fids, "konflikt", "Artikeln har ändrats i WordPress sedan kartläggningen. Kartlägg om sajten och granska förslaget igen.")
                resultat["konflikter"] += len(fids)
                continue
            fore = post["content"]["raw"]
            efter, lyckade = fore, []
            for r in grupp:
                try:
                    efter = infoga_lank(efter, r["ankartext"], r["stycke"], r["mal_url"])
                    lyckade.append(r)
                except LankFel as e:
                    satt([r["id"]], "fel", str(e))
                    resultat["fel"] += 1
            if not lyckade:
                continue
            ok_ids = [r["id"] for r in lyckade]
            if not skarp:
                satt(ok_ids, "simulerad", f"Simulering ({config.settings.publicering}): ändringen kontrollerades men skickades inte.")
                for r in lyckade:
                    db.logga(0, "lank_simulerad", f"{kalla['titel']} → {r['mal_url']} (”{r['ankartext']}”)")
                resultat["simulerade"] += len(lyckade)
                continue
            try:
                ny = await wp.uppdatera_innehall(kalla["typ"], kalla["wp_id"], efter)
                kontroll = await wp.hamta_redigerbar(kalla["typ"], kalla["wp_id"])
                if kontroll["content"]["raw"] != efter:
                    raise WordPressFel("WordPress sparade inte ändringen som väntat.")
            except WordPressFel as e:
                satt(ok_ids, "fel", str(e))
                resultat["fel"] += len(ok_ids)
                continue
            with db._conn() as c:
                c.execute("INSERT INTO lankandringar (kalla_id, wp_id, typ, forslag_ids, fore, efter, efter_andrad, status, tid) "
                          "VALUES (?, ?, ?, ?, ?, ?, ?, 'verkstalld', ?)",
                          (kid, kalla["wp_id"], kalla["typ"], json.dumps(ok_ids), fore, efter, kontroll.get("modified_gmt"), db.nu()))
                c.execute("UPDATE sajt_sidor SET andrad = ? WHERE id = ?", (kontroll.get("modified_gmt") or ny.get("modified_gmt"), kid))
                for r in lyckade:
                    c.execute("INSERT INTO sajt_lankar (fran_id, till_url, till_id, ankartext, status) VALUES (?, ?, ?, ?, 'ok')",
                              (kid, _sokvag(r["mal_url"]), r["mal_id"], r["ankartext"]))
            satt(ok_ids, "verkstalld")
            for r in lyckade:
                db.logga(0, "lank_tillagd", f"{kalla['titel']} → {r['mal_url']} (”{r['ankartext']}”)")
            resultat["verkstallda"] += len(lyckade)
    return resultat


def _gruppera(rader: list[dict]) -> dict[int, list[dict]]:
    grupper: dict[int, list[dict]] = {}
    for r in rader:
        grupper.setdefault(r["kalla_id"], []).append(r)
    return grupper


def andringar() -> list[dict]:
    with db._conn() as c:
        rows = c.execute("SELECT a.id, a.kalla_id, a.forslag_ids, a.status, a.tid, s.titel, s.url FROM lankandringar a "
                         "JOIN sajt_sidor s ON s.id = a.kalla_id ORDER BY a.id DESC").fetchall()
    return [{**dict(r), "forslag_ids": json.loads(r["forslag_ids"])} for r in rows]


async def aterstall(andring_id: int, transport=None) -> str:
    """Tar bort studions länkar igen. Manuella ändringar som gjorts efteråt bevaras."""
    with db._conn() as c:
        a = c.execute("SELECT * FROM lankandringar WHERE id = ? AND status = 'verkstalld'", (andring_id,)).fetchone()
        if not a:
            raise LankFel("Ändringen finns inte eller är redan återställd.")
        forslag = [dict(r) for r in c.execute(
            f"SELECT * FROM lankforslag WHERE id IN ({','.join('?' for _ in json.loads(a['forslag_ids']))})",
            json.loads(a["forslag_ids"]))]
    if config.settings.publicering != "skarp":
        raise LankFel("Återställning skriver till sajten och kräver publiceringsläget skarp.")
    async with WordPress(transport=transport) as wp:
        post = await wp.hamta_redigerbar(a["typ"], a["wp_id"])
        nu_raw = post["content"]["raw"]
        if post.get("modified_gmt") == a["efter_andrad"] and nu_raw == a["efter"]:
            ny_raw, satt_ = a["fore"], "Återställd till versionen före ändringen."
        else:
            # Artikeln har redigerats efteråt: ta bara bort studions länkar och behåll resten.
            ny_raw = nu_raw
            for f in forslag:
                ny_raw = ta_bort_lank(ny_raw, f["mal_url"], f["ankartext"]) or ny_raw
            if ny_raw == nu_raw:
                raise LankFel("Länkarna finns inte längre i artikeln; inget att återställa.")
            satt_ = "Studions länkar togs bort; senare redigeringar behölls."
        ny = await wp.uppdatera_innehall(a["typ"], a["wp_id"], ny_raw)
    with db._conn() as c:
        c.execute("UPDATE lankandringar SET status = 'aterstalld' WHERE id = ?", (andring_id,))
        c.execute("UPDATE sajt_sidor SET andrad = ? WHERE id = ?", (ny.get("modified_gmt"), a["kalla_id"]))
        for f in forslag:
            c.execute("UPDATE lankforslag SET status = 'aterstalld', uppdaterad = ? WHERE id = ?", (db.nu(), f["id"]))
            c.execute("DELETE FROM sajt_lankar WHERE fran_id = ? AND till_url = ? AND ankartext = ?",
                      (a["kalla_id"], _sokvag(f["mal_url"]), f["ankartext"]))
    db.logga(0, "lank_aterstalld", satt_)
    return satt_


def oversikt() -> dict:
    with db._conn() as c:
        rader = c.execute("SELECT status, COUNT(*) FROM lankforslag GROUP BY status").fetchall()
    return {r[0]: r[1] for r in rader}


def logg(antal: int = 50) -> list[dict]:
    with db._conn() as c:
        rows = c.execute("SELECT handling, kommentar, tid FROM handelser WHERE handling LIKE 'lank_%' ORDER BY id DESC LIMIT ?",
                         (antal,)).fetchall()
    return [dict(r) for r in rows]
