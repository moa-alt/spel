"""AI-förslag för SEO-kartan och internlänkar i nya artiklar.

AI-förslag märks alltid som förslag. AI:n får bara föreslå länkar till adresser som finns i
den senaste kartläggningen; alla andra tas bort innan något sparas eller visas. Den får inte
hitta på sökvolymer, konkurrensvärden eller andra mätvärden.
"""

import json
import re
from html import unescape

import anthropic

from . import config, db, sajtkarta
from .planering import likhet

SCHEMA = """
CREATE TABLE IF NOT EXISTS seo_forslag (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kluster     TEXT NOT NULL,
    huvudsokord TEXT NOT NULL,
    data        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'ny',     -- ny, i_kon, avvisad
    artikel_id  INTEGER,
    skapad      TEXT NOT NULL
);
-- Planerade internlänkar för artiklar i kön: vart den nya artikeln ska länka och varifrån.
CREATE TABLE IF NOT EXISTS artikel_lankplan (
    artikel_id  INTEGER PRIMARY KEY,
    lanka_till  TEXT NOT NULL DEFAULT '[]',
    lanka_fran  TEXT NOT NULL DEFAULT '[]'
);
"""

FORSLAG_SCHEMA = {
    "type": "object",
    "properties": {
        "forslag": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kluster": {"type": "string"},
                    "huvudsokord": {"type": "string"},
                    "sekundara_sokord": {"type": "array", "items": {"type": "string"}},
                    "sokintention": {"type": "string", "enum": ["informativ", "kommersiell", "transaktionell", "navigerande"]},
                    "motivering": {"type": "string"},
                    "lanka_till": {"type": "array", "items": {"type": "string"}},
                    "lanka_fran": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["kluster", "huvudsokord", "sekundara_sokord", "sokintention", "motivering", "lanka_till", "lanka_fran"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["forslag"],
    "additionalProperties": False,
}

SYSTEM = """Du är SEO-strateg för Klimathemmet (klimathemmet.se), en svensk sajt om inomhusklimat, ventilation, uppvärmning, värmepumpar, isolering, fukt och energibesparing.

Uppgift: föreslå nya artiklar som stärker sajtens befintliga ämneskluster (topical authority).

Regler:
- Föreslå bara ämnen som täcker en verklig lucka i klustret och som inte konkurrerar med en befintlig artikel eller ett sökord som redan ligger i kön om samma primära sökintention.
- Skriv huvudsökord och sekundära sökord på naturlig svenska, så som folk söker.
- Hitta aldrig på sökvolymer, konkurrensvärden, klickfrekvenser eller andra mätvärden, och påstå inte att ett sökord har en viss volym.
- motivering: 1–2 meningar om varför ämnet stärker klustret, med hänvisning till befintliga artiklar.
- lanka_till: 2–5 befintliga adresser som den nya artikeln naturligt bör länka till, gärna klustrets pelarsida och relevanta kommersiella sidor.
- lanka_fran: 1–4 befintliga artiklar där en länk till den nya artikeln skulle passa naturligt.
- Använd bara adresser exakt som de står i underlaget. Hitta aldrig på adresser.
- Högst 3 förslag per kluster, totalt högst 12. Hellre färre och bättre."""


def init() -> None:
    with db._conn() as c:
        c.executescript(SCHEMA)


class ForslagsFel(Exception):
    pass


def _underlag(analys: dict, ko: list[str]) -> str:
    per_id = {n["id"]: n for n in analys["noder"]}
    rader = []
    for k in analys["kluster"][:12]:
        if k["id"] == "ovrigt":
            continue
        medlemmar = [n for n in analys["noder"] if n["kluster"] == k["id"] and n["typ"] != "kategori"]
        if not medlemmar:
            continue
        rader.append(f"\n## Kluster: {k['namn']} ({len(medlemmar)} sidor)")
        if k["pelare"]:
            rader.append(f"Pelarsida: {per_id[k['pelare']]['titel']} – {per_id[k['pelare']]['url']}")
        for n in sorted(medlemmar, key=lambda n: -n["in"])[:25]:
            markering = " [kommersiell]" if n["kommersiell"] else ""
            rader.append(f"- {n['titel']} – {n['url']}{markering}")
    kommersiella = [n for n in analys["noder"] if n["kommersiell"]]
    if kommersiella:
        rader.append("\n## Viktiga kommersiella sidor")
        rader.extend(f"- {n['titel']} – {n['url']}" for n in kommersiella)
    if ko:
        rader.append("\n## Sökord som redan ligger i artikelkön (föreslå inte samma sak)")
        rader.extend(f"- {s}" for s in ko)
    return "\n".join(rader)


async def foresla_artiklar() -> list[dict]:
    if not config.settings.anthropic_api_key:
        raise ForslagsFel("AI-förslag kräver ANTHROPIC_API_KEY.")
    analys = sajtkarta.analysera()
    if not analys["noder"]:
        raise ForslagsFel("Kartlägg sajten först.")
    ko = [a["huvudsokord"] for a in db.lista()]
    client = anthropic.AsyncAnthropic(api_key=config.settings.anthropic_api_key, max_retries=3)
    try:
        async with client.messages.stream(
            model=config.settings.claude_model,
            max_tokens=32000,
            thinking={"type": "adaptive"},
            output_config={"effort": config.settings.claude_effort, "format": {"type": "json_schema", "schema": FORSLAG_SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content": "Underlag från den senaste kartläggningen:\n" + _underlag(analys, ko)}],
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        ) as stream:
            message = await stream.get_final_message()
    except anthropic.AuthenticationError:
        raise ForslagsFel("API-nyckeln godkändes inte.")
    except anthropic.RateLimitError:
        raise ForslagsFel("För många anrop just nu. Försök igen om en stund.")
    except anthropic.APIStatusError as e:
        raise ForslagsFel(f"Anropet till Claude misslyckades ({e.status_code}).")
    except anthropic.APIConnectionError:
        raise ForslagsFel("Kunde inte nå Claude.")
    if message.stop_reason in ("refusal", "max_tokens"):
        raise ForslagsFel("Claude kunde inte slutföra förslagen. Försök igen.")
    blocks = list(message.content)
    sista = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    try:
        data = json.loads("".join(b.text for b in blocks[sista + 1:] if b.type == "text"))
    except json.JSONDecodeError:
        raise ForslagsFel("Svaret från Claude kunde inte tolkas.")
    return spara_forslag(data.get("forslag", []), analys)


def spara_forslag(forslag: list[dict], analys: dict | None = None) -> list[dict]:
    """Validerar och sparar AI-förslag: okända adresser tas bort, krockar med befintligt innehåll märks."""
    analys = analys or sajtkarta.analysera()
    kanda = sajtkarta.kanda_urler()
    titlar = [n["titel"] for n in analys["noder"] if n["typ"] != "kategori"]
    ko = [a["huvudsokord"] for a in db.lista()]
    sparade = []
    with db._conn() as c:
        c.execute("DELETE FROM seo_forslag WHERE status = 'ny'")
        for f in forslag[:12]:
            huvud = " ".join(f.get("huvudsokord", "").split())[:120]
            if len(huvud) < 2:
                continue
            krockar = [t for t in titlar + ko if likhet(huvud, t) >= 0.75]
            data = {
                "sekundara_sokord": [s.strip() for s in f.get("sekundara_sokord", []) if s.strip()][:10],
                "sokintention": f.get("sokintention"),
                "motivering": f.get("motivering", "").strip(),
                "lanka_till": [u for u in dict.fromkeys(f.get("lanka_till", [])) if u in kanda][:5],
                "lanka_fran": [u for u in dict.fromkeys(f.get("lanka_fran", [])) if u in kanda][:4],
                "borttagna_lankar": len([u for u in f.get("lanka_till", []) + f.get("lanka_fran", []) if u not in kanda]),
                "krockar_med": krockar[:3],
            }
            cur = c.execute("INSERT INTO seo_forslag (kluster, huvudsokord, data, skapad) VALUES (?, ?, ?, ?)",
                            (f.get("kluster", "")[:120], huvud, json.dumps(data, ensure_ascii=False), db.nu()))
            sparade.append({"id": cur.lastrowid, "kluster": f.get("kluster", ""), "huvudsokord": huvud, **data})
    return sparade


def lista_forslag() -> list[dict]:
    with db._conn() as c:
        rows = c.execute("SELECT * FROM seo_forslag WHERE status = 'ny' ORDER BY id").fetchall()
    return [{"id": r["id"], "kluster": r["kluster"], "huvudsokord": r["huvudsokord"], **json.loads(r["data"])} for r in rows]


def till_kon(forslag_id: int) -> int:
    with db._conn() as c:
        r = c.execute("SELECT * FROM seo_forslag WHERE id = ? AND status = 'ny'", (forslag_id,)).fetchone()
    if not r:
        raise ForslagsFel("Förslaget finns inte eller är redan hanterat.")
    data = json.loads(r["data"])
    artikel_id = db.skapa(r["huvudsokord"], data["sekundara_sokord"])
    with db._conn() as c:
        c.execute("UPDATE seo_forslag SET status = 'i_kon', artikel_id = ? WHERE id = ?", (artikel_id, forslag_id))
        c.execute("INSERT OR REPLACE INTO artikel_lankplan (artikel_id, lanka_till, lanka_fran) VALUES (?, ?, ?)",
                  (artikel_id, json.dumps(data["lanka_till"]), json.dumps(data["lanka_fran"])))
    db.logga(artikel_id, "fran_seo_karta", data.get("motivering") or None)
    return artikel_id


def avvisa(forslag_id: int) -> None:
    with db._conn() as c:
        c.execute("UPDATE seo_forslag SET status = 'avvisad' WHERE id = ? AND status = 'ny'", (forslag_id,))


def lankplan(artikel_id: int) -> dict:
    with db._conn() as c:
        r = c.execute("SELECT * FROM artikel_lankplan WHERE artikel_id = ?", (artikel_id,)).fetchone()
    return {"lanka_till": json.loads(r["lanka_till"]), "lanka_fran": json.loads(r["lanka_fran"])} if r else {"lanka_till": [], "lanka_fran": []}


# ---------- Internlänkar i nya artiklar ----------

def lankmal_for(artikel: dict) -> list[dict]:
    """Adresser som den nya artikeln får länka till: planerade länkar först, sedan de mest relevanta sidorna."""
    plan = lankplan(artikel["id"])["lanka_till"]
    forslag = sajtkarta.lankmal(artikel["huvudsokord"], artikel["sekundara_sokord"], antal=10)
    titel_for = {f["url"]: f["titel"] for f in forslag}
    with db._conn() as c:
        for url in plan:
            r = c.execute("SELECT titel FROM sajt_sidor WHERE url = ?", (url,)).fetchone()
            if r:
                titel_for.setdefault(url, r[0])
    ordning = [u for u in plan if u in titel_for] + [f["url"] for f in forslag if f["url"] not in plan]
    return [{"url": u, "titel": titel_for[u]} for u in ordning[:10]]


_LANK = re.compile(r"<a\b([^>]*)>(.*?)</a>", re.S | re.I)
_HREF = re.compile(r'href="([^"]*)"')


def kontrollera_lankar(html: str, tillatna: set[str]) -> tuple[str, list[dict]]:
    """Behåller bara länkar till kända adresser, högst en länk per adress. Andra länkar blir vanlig text."""
    sedda: set[str] = set()
    lankar: list[dict] = []

    def ersatt(m: re.Match) -> str:
        href = _HREF.search(m.group(1))
        url = unescape(href.group(1)) if href else ""
        if url.startswith("#"):
            return m.group(0)  # innehållsförteckningen
        if url in tillatna and url not in sedda:
            sedda.add(url)
            lankar.append({"url": url, "ankartext": " ".join(re.sub(r"<[^>]+>", "", unescape(m.group(2))).split())})
            return m.group(0)
        return m.group(2)

    return _LANK.sub(ersatt, html), lankar


def lankkontroll(html: str) -> list[dict]:
    """Artikelns länkar till sajten, och om målsidan finns i den senaste kartläggningen."""
    bas = config.settings.wp_url.rstrip("/")
    kanda = sajtkarta.kanda_urler()
    ut = []
    for m in _LANK.finditer(html or ""):
        href = _HREF.search(m.group(1))
        url = unescape(href.group(1)) if href else ""
        if not url or url.startswith("#") or (bas and not url.startswith(bas)):
            continue
        ankare = " ".join(re.sub(r"<[^>]+>", "", unescape(m.group(2))).split())
        ut.append({"url": url, "ankartext": ankare, "finns": url in kanda if kanda else None})
    return ut
