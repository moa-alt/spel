"""Saknade mål-URL: adresser som befintliga artiklar länkar till men som inte finns (404/410).

En saknad adress klassificeras som
- planerad: bara om en artikel i artikelkön ska publiceras på den adressen, eller om du själv
  har bekräftat den som planerad;
- trasig: i alla andra fall.

Ingenting här skapar eller publicerar artiklar automatiskt. "Skapa artikel" lägger, efter ditt
godkännande, en artikel som utkast i artikelkön; generering och publicering är separata steg.
När artikeln väl publicerats på rätt adress blir länkarna fungerande vid nästa kartläggning.
"""

import json
import logging
import re
from collections import Counter

import anthropic

from . import config, db, sajtkarta
from .demo import slugify
from .planering import _token

log = logging.getLogger("studio.saknade")

SCHEMA = """
CREATE TABLE IF NOT EXISTS planerade_mal (
    sokvag      TEXT PRIMARY KEY,          -- t.ex. /fukt-i-krypgrund/
    artikel_id  INTEGER,                   -- artikeln i kön som ska publiceras här (om någon)
    status      TEXT NOT NULL,             -- planerad, publicerad
    bekraftad   TEXT NOT NULL,
    uppdaterad  TEXT NOT NULL
);
"""

KOMMERSIELLA_ORD = ("pris", "priser", "kostnad", "kostar", "köpa", "köp", "bäst", "bästa", "offert", "test", "jämför", "billig")
TRANSAKTIONELLA_ORD = ("beställ", "boka", "offert", "rabatt")


class SaknadFel(Exception):
    pass


def init() -> None:
    with db._conn() as c:
        c.executescript(SCHEMA)
        # Adress som artikeln måste publiceras på (sätts när artikeln skapas från en saknad URL).
        db.lagg_till_kolumn(c, "artiklar", "mal_slug", "TEXT")


def _sista_del(sokvag: str) -> str:
    return sokvag.strip("/").rsplit("/", 1)[-1]


def _bas() -> str:
    return config.settings.wp_url.rstrip("/")


# ---------- Klassificering ----------

def _ko_slugar() -> dict[str, dict]:
    """Slug → artikel för artiklar i kön som inte är avvisade. Den låsta adressen gäller före innehållets slug."""
    ut: dict[str, dict] = {}
    with db._conn() as c:
        rader = c.execute("SELECT id, huvudsokord, status, innehall, mal_slug FROM artiklar WHERE status != 'avvisad'").fetchall()
    for r in rader:
        innehall = json.loads(r["innehall"]) if r["innehall"] else {}
        for slug in (r["mal_slug"], innehall.get("slug"), slugify(r["huvudsokord"])):
            if slug:
                ut.setdefault(slug, {"id": r["id"], "huvudsokord": r["huvudsokord"], "status": r["status"]})
    return ut


def _planerade_rader() -> dict[str, dict]:
    with db._conn() as c:
        return {r["sokvag"]: dict(r) for r in c.execute("SELECT * FROM planerade_mal")}


def planerade_sokvagar() -> set[str]:
    """Saknade adresser som räknas som planerade (används av SEO-kartans nyckeltal)."""
    return {m["sokvag"] for m in saknade() if m["klass"] == "planerad"}


# ---------- Förslag ----------

def _sokintention(text: str) -> str:
    ord_ = set(re.findall(r"[a-zåäö]+", text.lower()))
    if ord_ & set(TRANSAKTIONELLA_ORD):
        return "transaktionell"
    if ord_ & set(KOMMERSIELLA_ORD):
        return "kommersiell"
    return "informativ"


def _aterstall_tecken(slugord: list[str], ankare: list[str]) -> str:
    """Slugen saknar å, ä och ö; hämta rätt stavning från ankartexterna när det går."""
    kandidater = {slugify(o): o for a in ankare for o in re.findall(r"[\wåäöÅÄÖ-]+", a.lower())}
    return " ".join(kandidater.get(o, o) for o in slugord)


def berakna_forslag(mal: dict, analys: dict) -> dict:
    """Förslag utan AI, byggt på adressen, ankartexterna och artiklarna som länkar hit."""
    ankare = [k["ankartext"] for k in mal["kallor"] if k["ankartext"]]
    slugord = [o for o in _sista_del(mal["sokvag"]).split("-") if o]
    fran_slug = _aterstall_tecken(slugord, ankare)
    # Vanligaste ankartexten med minst två ord, annars orden i adressen.
    vanliga = [a for a, _ in Counter(" ".join(a.lower().split()) for a in ankare).most_common() if len(a.split()) >= 2]
    huvud = vanliga[0] if vanliga and len(_token(vanliga[0]) & _token(fran_slug)) >= max(1, len(_token(fran_slug)) // 2) else fran_slug
    sekundara = [a for a in dict.fromkeys(vanliga + [fran_slug]) if a != huvud][:5]
    kluster_id = Counter(k["kluster"] for k in mal["kallor"] if k.get("kluster")).most_common(1)
    kid = kluster_id[0][0] if kluster_id else None
    namn = {k["id"]: k["namn"] for k in analys["kluster"]}
    return {
        "huvudsokord": huvud, "sekundara_sokord": sekundara, "sokintention": _sokintention(huvud + " " + " ".join(ankare)),
        "kluster": namn.get(kid) if kid else None, "kluster_id": kid, "ursprung": "beraknat",
        "motivering": f"{len(mal['kallor'])} befintliga artiklar länkar redan hit med ankartexter som ”{ankare[0]}”." if ankare else "",
    }


def _prioritet(mal: dict, forslag: dict, analys: dict) -> dict:
    """Högre poäng för fler inkommande länkar, länkar från pelarsidor, stora kluster och kommersiell sökintention."""
    noder = {n["id"]: n for n in analys["noder"]}
    kallor = {k["id"] for k in mal["kallor"]}
    fran_pelare = any(noder.get(i, {}).get("roll") == "pelare" for i in kallor)
    klusterstorlek = next((k["antal"] for k in analys["kluster"] if k["id"] == forslag.get("kluster_id")), 0)
    storsta = max((k["antal"] for k in analys["kluster"]), default=1) or 1
    relevans = len(_token(forslag["huvudsokord"]) & _token(" ".join(noder[i]["titel"] for i in kallor if i in noder)))
    delar = [
        (min(len(kallor), 10) * 3, f"{len(kallor)} {'artikel' if len(kallor) == 1 else 'artiklar'} länkar hit"),
        (4 if fran_pelare else 0, "länkas från en pelarsida"),
        (round(4 * klusterstorlek / storsta), "stort ämneskluster" if klusterstorlek / storsta >= 0.5 else ""),
        (3 if forslag["sokintention"] in ("kommersiell", "transaktionell") else 0, "kommersiell sökintention"),
        (min(relevans, 3), "nära ämnet i länkande artiklar"),
    ]
    poang = sum(p for p, _ in delar)
    return {"poang": poang, "niva": "hög" if poang >= 14 else "medel" if poang >= 8 else "låg",
            "skal": [t for p, t in delar if p and t]}


def saknade() -> list[dict]:
    """Alla saknade mål-URL, grupperade med sina inkommande länkar, klassificerade och prioriterade."""
    analys = sajtkarta.analysera(utan_saknade=True)
    noder = {n["id"]: n for n in analys["noder"]}
    with db._conn() as c:
        rader = c.execute(
            "SELECT l.till_url, l.fran_id, l.ankartext, s.titel, s.url FROM sajt_lankar l "
            "JOIN sajt_sidor s ON s.id = l.fran_id WHERE l.status = 'trasig' ORDER BY l.till_url").fetchall()
        kanda = {r[0] for r in c.execute("SELECT sokvag FROM sajt_sidor")}
    grupper: dict[str, dict] = {}
    for r in rader:
        g = grupper.setdefault(r["till_url"], {"sokvag": r["till_url"], "url": _bas() + r["till_url"], "kallor": []})
        if r["fran_id"] not in {k["id"] for k in g["kallor"]}:
            g["kallor"].append({"id": r["fran_id"], "titel": r["titel"], "url": r["url"], "ankartext": r["ankartext"],
                                "kluster": noder.get(r["fran_id"], {}).get("kluster")})

    planerade = _planerade_rader()
    # En planerad adress som nu finns på sajten är publicerad; länkarna dit är då redan fungerande.
    for sokvag, p in planerade.items():
        if sokvag in kanda and p["status"] != "publicerad":
            with db._conn() as c:
                c.execute("UPDATE planerade_mal SET status = 'publicerad', uppdaterad = ? WHERE sokvag = ?", (db.nu(), sokvag))
    ko = _ko_slugar()
    ut = []
    for sokvag, g in grupper.items():
        artikel = None
        p = planerade.get(sokvag)
        if p and p["artikel_id"]:
            artikel = db.hamta(p["artikel_id"])
        if artikel is None:
            artikel = ko.get(_sista_del(sokvag))
        elif artikel["status"] == "avvisad":
            artikel = None
        klass = "planerad" if artikel or (p and p["status"] == "planerad") else "trasig"
        g["antal"] = len(g["kallor"])
        g["klass"] = klass
        g["bekraftad"] = bool(p)
        g["artikel"] = {"id": artikel["id"], "huvudsokord": artikel["huvudsokord"], "status": artikel["status"]} if artikel else None
        g["forslag"] = berakna_forslag(g, analys)
        g["prioritet"] = _prioritet(g, g["forslag"], analys)
        ut.append(g)
    ut.sort(key=lambda m: (-m["prioritet"]["poang"], -m["antal"], m["sokvag"]))
    return ut


def hitta(sokvag: str) -> dict:
    mal = next((m for m in saknade() if m["sokvag"] == sokvag), None)
    if mal is None:
        raise SaknadFel("Adressen finns inte bland de saknade mål-URL:erna. Kartlägg sajten igen.")
    return mal


# ---------- Åtgärder (alltid på din begäran) ----------

def markera_planerad(sokvag: str) -> None:
    hitta(sokvag)
    with db._conn() as c:
        c.execute("INSERT INTO planerade_mal (sokvag, artikel_id, status, bekraftad, uppdaterad) VALUES (?, NULL, 'planerad', ?, ?) "
                  "ON CONFLICT(sokvag) DO UPDATE SET status = 'planerad', uppdaterad = excluded.uppdaterad",
                  (sokvag, db.nu(), db.nu()))
    db.logga(0, "mal_planerad", sokvag)


def ta_bort_planering(sokvag: str) -> None:
    with db._conn() as c:
        c.execute("DELETE FROM planerade_mal WHERE sokvag = ?", (sokvag,))
    db.logga(0, "mal_planering_borttagen", sokvag)


def skapa_artikel(sokvag: str, huvudsokord: str, sekundara: list[str], sokintention: str = "", kluster: str = "") -> int:
    """Lägger en artikel som utkast i artikelkön, låst till den saknade adressen. Genererar och publicerar inte."""
    mal = hitta(sokvag)
    if mal["artikel"]:
        raise SaknadFel(f"Det finns redan en planerad artikel för {sokvag}: ”{mal['artikel']['huvudsokord']}”.")
    artikel_id = db.skapa(huvudsokord, sekundara)
    with db._conn() as c:
        c.execute("UPDATE artiklar SET mal_slug = ? WHERE id = ?", (_sista_del(sokvag), artikel_id))
        c.execute("INSERT INTO planerade_mal (sokvag, artikel_id, status, bekraftad, uppdaterad) VALUES (?, ?, 'planerad', ?, ?) "
                  "ON CONFLICT(sokvag) DO UPDATE SET artikel_id = excluded.artikel_id, status = 'planerad', uppdaterad = excluded.uppdaterad",
                  (sokvag, artikel_id, db.nu(), db.nu()))
        # De befintliga artiklarna länkar redan hit; de blir inkommande länkar så fort artikeln är publicerad.
        c.execute("INSERT OR REPLACE INTO artikel_lankplan (artikel_id, lanka_till, lanka_fran) VALUES (?, '[]', ?)",
                  (artikel_id, json.dumps([k["url"] for k in mal["kallor"]])))
    detaljer = ", ".join(x for x in (f"sökintention {sokintention}" if sokintention else "", f"kluster {kluster}" if kluster else "") if x)
    db.logga(artikel_id, "fran_saknad_url",
             f"Skapad för {sokvag}, som {mal['antal']} artiklar redan länkar till" + (f" ({detaljer})" if detaljer else ""))
    return artikel_id


def artikel_publicerad(artikel_id: int, lank: str) -> str | None:
    """Kontrollerar att en artikel som skapats för en saknad adress hamnade på rätt adress."""
    with db._conn() as c:
        r = c.execute("SELECT sokvag FROM planerade_mal WHERE artikel_id = ?", (artikel_id,)).fetchone()
    if not r:
        return None
    faktisk = sajtkarta.normalisera(lank, _bas() + "/") if lank else None
    if faktisk != r["sokvag"]:
        varning = f"Artikeln publicerades på {faktisk or lank}, inte på {r['sokvag']}; de befintliga länkarna fungerar därför inte."
        db.logga(artikel_id, "mal_fel_adress", varning)
        return varning
    db.logga(artikel_id, "mal_publicerad", f"Publicerad på {r['sokvag']}; länkarna dit blir fungerande vid nästa kartläggning.")
    return None


# ---------- AI-förslag ----------

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "huvudsokord": {"type": "string"},
        "sekundara_sokord": {"type": "array", "items": {"type": "string"}},
        "sokintention": {"type": "string", "enum": ["informativ", "kommersiell", "transaktionell", "navigerande"]},
        "kluster": {"type": "string"},
        "motivering": {"type": "string"},
    },
    "required": ["huvudsokord", "sekundara_sokord", "sokintention", "kluster", "motivering"],
    "additionalProperties": False,
}


async def ai_forslag(sokvag: str) -> dict:
    """AI-förslag på huvudnyckelord m.m. för en saknad adress. Skapar ingen artikel."""
    if not config.settings.anthropic_api_key:
        raise SaknadFel("AI-förslag kräver ANTHROPIC_API_KEY.")
    mal = hitta(sokvag)
    analys = sajtkarta.analysera(utan_saknade=True)
    klusternamn = [k["namn"] for k in analys["kluster"] if k["id"] != "ovrigt"]
    kallor = "\n".join(f"- {k['titel']}: länkar med ”{k['ankartext']}”" for k in mal["kallor"][:20])
    prompt = (f"En sida saknas på klimathemmet.se: {sokvag}\nBefintliga artiklar länkar redan dit:\n{kallor}\n\n"
              f"Ämneskluster på sajten: {', '.join(klusternamn)}\n\n"
              "Föreslå artikeln som bör publiceras på adressen: huvudnyckelord som det skrivs naturligt på svenska och som "
              "passar adressen, 3–6 sekundära sökord, sökintention, vilket av klustren den hör till (exakt namn) och en "
              "kort motivering. Hitta inte på sökvolymer eller andra mätvärden.")
    client = anthropic.AsyncAnthropic(api_key=config.settings.anthropic_api_key, max_retries=3)
    try:
        async with client.messages.stream(
            model=config.settings.claude_model, max_tokens=8000, thinking={"type": "adaptive"},
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": AI_SCHEMA}},
            messages=[{"role": "user", "content": prompt}],
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"}, extra_body={"fallbacks": "default"},
        ) as stream:
            message = await stream.get_final_message()
    except anthropic.APIError as e:
        raise SaknadFel(f"Anropet till Claude misslyckades ({type(e).__name__}).")
    if message.stop_reason in ("refusal", "max_tokens"):
        raise SaknadFel("Claude kunde inte ge något förslag. Försök igen.")
    blocks = list(message.content)
    sista = max((i for i, b in enumerate(blocks) if b.type == "fallback"), default=-1)
    try:
        data = json.loads("".join(b.text for b in blocks[sista + 1:] if b.type == "text"))
    except json.JSONDecodeError:
        raise SaknadFel("Svaret från Claude kunde inte tolkas.")
    kluster = data["kluster"] if data["kluster"] in klusternamn else mal["forslag"]["kluster"]
    return {"huvudsokord": " ".join(data["huvudsokord"].split())[:120],
            "sekundara_sokord": [s.strip() for s in data["sekundara_sokord"] if s.strip()][:8],
            "sokintention": data["sokintention"], "kluster": kluster, "motivering": data["motivering"].strip(),
            "ursprung": "ai"}
