"""Kartläggning av klimathemmet.se och analys av länkstrukturen.

Allt här läser från sajten; ingenting ändras. Publicerat innehåll hämtas via WordPress
öppna REST API (inloggning används om den finns, men behövs inte).

Resultatet delas upp i två slag, så att du ser vad som är fakta och vad som är bedömningar:
- verifierat: sådant som går att mäta i sajtens innehåll (länkar, trasiga länkar,
  sidor utan inkommande länkar, saknad metabeskrivning).
- beräknat: bedömningar från textlikhet (kluster för sidor, pelarsidor, möjlig
  kannibalisering, saknade länkar). Ska kontrolleras av dig.
"""

import asyncio
import json
import logging
import re
from collections import defaultdict
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse

import httpx

from . import config, db
from .planering import _token, likhet

log = logging.getLogger("studio.sajtkarta")

MAX_SIDOR_PER_TYP = 50  # 50 × 100 = 5 000 objekt per typ
MAX_LANKKONTROLLER = 200
HOPPA_OVER = ("/wp-content/", "/wp-admin/", "/wp-json/", "/feed", "/tag/", "/author/", "/page/", "?")

SCHEMA = """
CREATE TABLE IF NOT EXISTS sajt_sidor (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    wp_id         INTEGER,
    typ           TEXT NOT NULL,          -- post, page, kategori
    url           TEXT NOT NULL UNIQUE,
    sokvag        TEXT NOT NULL,
    titel         TEXT NOT NULL,
    text          TEXT NOT NULL DEFAULT '',
    rubriker      TEXT NOT NULL DEFAULT '[]',
    kategorier    TEXT NOT NULL DEFAULT '[]',
    seo_titel     TEXT,
    seo_beskrivning TEXT,
    ord           INTEGER NOT NULL DEFAULT 0,
    andrad        TEXT
);
CREATE TABLE IF NOT EXISTS sajt_lankar (
    fran_id   INTEGER NOT NULL,
    till_url  TEXT NOT NULL,
    till_id   INTEGER,
    ankartext TEXT NOT NULL DEFAULT '',
    status    TEXT NOT NULL              -- ok, trasig, okand
);
CREATE INDEX IF NOT EXISTS sajt_lankar_fran ON sajt_lankar(fran_id);
CREATE INDEX IF NOT EXISTS sajt_lankar_till ON sajt_lankar(till_id);
CREATE TABLE IF NOT EXISTS sajt_markningar (
    url          TEXT PRIMARY KEY,
    kommersiell  INTEGER NOT NULL DEFAULT 0
);
"""


class KartFel(Exception):
    pass


def init() -> None:
    with db._conn() as c:
        c.executescript(SCHEMA)
        # Tillagt i senare version: stycken (för exakt placering av länkförslag) och fokusnyckelord.
        db.lagg_till_kolumn(c, "sajt_sidor", "stycken", "TEXT NOT NULL DEFAULT '[]'")
        db.lagg_till_kolumn(c, "sajt_sidor", "fokus", "TEXT")


# ---------- Hämtning ----------

class _LankParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.lankar: list[tuple[str, str]] = []
        self.rubriker: list[str] = []
        self.text: list[str] = []
        self._href: str | None = None
        self._ankare: list[str] = []
        self._rubrik: list[str] | None = None
        self.stycken: list[str] = []
        self._stycke: list[str] | None = None
        self._dold = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._dold += 1
        elif tag == "a":
            self._href = dict(attrs).get("href")
            self._ankare = []
        elif tag in ("h2", "h3"):
            self._rubrik = []
        if tag in ("p", "li") and self._stycke is None:
            self._stycke = []

    def handle_endtag(self, tag):
        if tag in ("p", "li") and self._stycke is not None:
            text = " ".join("".join(self._stycke).split())
            if len(text.split()) >= 6:
                self.stycken.append(text)
            self._stycke = None
        if tag in ("script", "style"):
            self._dold = max(0, self._dold - 1)
        elif tag == "a" and self._href is not None:
            self.lankar.append((self._href, " ".join("".join(self._ankare).split())))
            self._href = None
        elif tag in ("h2", "h3") and self._rubrik is not None:
            self.rubriker.append(" ".join("".join(self._rubrik).split()))
            self._rubrik = None

    def handle_data(self, data):
        if self._dold:
            return
        self.text.append(data)
        if self._href is not None:
            self._ankare.append(data)
        if self._rubrik is not None:
            self._rubrik.append(data)
        if self._stycke is not None:
            self._stycke.append(data)


def tolka_innehall(html: str) -> dict:
    p = _LankParser()
    p.feed(html or "")
    text = " ".join(" ".join(p.text).split())
    return {"lankar": p.lankar, "rubriker": p.rubriker, "text": text, "ord": len(text.split()), "stycken": p.stycken}


def _fokus(o: dict) -> str | None:
    """Fokusnyckelord från Yoast eller Rank Math, om tilläggspluginet gör fältet läsbart."""
    meta = o.get("meta") or {}
    varde = meta.get("_yoast_wpseo_focuskw") or (meta.get("rank_math_focus_keyword") or "").split(",")[0]
    return varde.strip() or None if isinstance(varde, str) else None


def _rensa_titel(text: str) -> str:
    return unescape(re.sub(r"<[^>]+>", "", text or "")).strip()


def normalisera(url: str, bas: str) -> str | None:
    """Gör om en länk till en jämförbar sökväg på sajten, eller None om den pekar utanför."""
    url = urldefrag(urljoin(bas, url.strip()))[0]
    p, b = urlparse(url), urlparse(bas)
    if p.scheme not in ("http", "https") or p.netloc.removeprefix("www.") != b.netloc.removeprefix("www."):
        return None
    sokvag = p.path or "/"
    if any(h in sokvag for h in HOPPA_OVER) or p.query or re.search(r"\.(jpe?g|png|webp|gif|svg|pdf|zip)$", sokvag, re.I):
        return None
    return sokvag.rstrip("/") + "/"


class Hamtare:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        s = config.settings
        if not s.wp_url:
            raise KartFel("WP_URL är inte satt. Ange sajtens adress, till exempel https://klimathemmet.se.")
        self.bas = s.wp_url.rstrip("/") + "/"
        auth = (s.wp_user, s.wp_app_password.replace(" ", "")) if s.wp_user and s.wp_app_password else None
        self._k = httpx.AsyncClient(base_url=self.bas, auth=auth, timeout=30.0, transport=transport,
                                    headers={"User-Agent": "Klimathemmet-AI-Studio/1.0"}, follow_redirects=True)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._k.aclose()

    async def _alla(self, typ: str, falt: str, extra: dict | None = None) -> list[dict]:
        ut = []
        for sida in range(1, MAX_SIDOR_PER_TYP + 1):
            try:
                r = await self._k.get(f"wp-json/wp/v2/{typ}", params={"per_page": 100, "page": sida, "_fields": falt, **(extra or {})})
            except httpx.HTTPError:
                raise KartFel("Kunde inte nå sajtens REST API.")
            if r.status_code == 400 and sida > 1:
                break  # förbi sista sidan
            if r.status_code >= 400:
                raise KartFel(f"Sajtens REST API svarade med fel {r.status_code} för {typ}.")
            data = r.json()
            ut.extend(data)
            if sida >= int(r.headers.get("X-WP-TotalPages", "1")) or not data:
                break
        return ut

    async def hamta(self) -> dict:
        falt = "id,link,slug,title,content,modified,modified_gmt,yoast_head_json,meta"
        poster = await self._alla("posts", falt + ",categories", {"status": "publish"})
        sidor = await self._alla("pages", falt, {"status": "publish"})
        kategorier = await self._alla("categories", "id,link,name,slug,count,description", {"hide_empty": "true"})
        return {"poster": poster, "sidor": sidor, "kategorier": kategorier}

    async def status(self, url: str) -> int | None:
        try:
            r = await self._k.head(url)
            if r.status_code == 405:
                r = await self._k.get(url)
            return r.status_code
        except httpx.HTTPError:
            return None


async def kartlagg(transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Hämtar hela sajten och ersätter den sparade kartan. Läser bara, ändrar ingenting på sajten."""
    async with Hamtare(transport) as h:
        data = await h.hamta()
        bas = h.bas

        sidor: list[dict] = []
        for typ, objekt in (("post", data["poster"]), ("page", data["sidor"])):
            for o in objekt:
                innehall = tolka_innehall(o.get("content", {}).get("rendered", ""))
                yoast = o.get("yoast_head_json") or {}
                sidor.append({
                    "wp_id": o["id"], "typ": typ, "url": o["link"], "sokvag": normalisera(o["link"], bas) or "/",
                    "titel": _rensa_titel(o.get("title", {}).get("rendered", "")), "text": innehall["text"][:20000],
                    "rubriker": innehall["rubriker"], "kategorier": o.get("categories", []),
                    "seo_titel": yoast.get("title"), "seo_beskrivning": yoast.get("description"),
                    "ord": innehall["ord"], "andrad": o.get("modified_gmt") or o.get("modified"), "_lankar": innehall["lankar"],
                    "stycken": innehall["stycken"][:200], "fokus": _fokus(o),
                })
        for k in data["kategorier"]:
            sidor.append({
                "wp_id": k["id"], "typ": "kategori", "url": k["link"], "sokvag": normalisera(k["link"], bas) or "/",
                "titel": _rensa_titel(k.get("name", "")), "text": _rensa_titel(k.get("description", "")),
                "rubriker": [], "kategorier": [k["id"]], "seo_titel": None, "seo_beskrivning": None,
                "ord": 0, "andrad": None, "_lankar": [], "stycken": [], "fokus": None,
            })

        kanda = {s["sokvag"] for s in sidor}
        okanda = {}
        for s in sidor:
            for href, _ in s["_lankar"]:
                sokvag = normalisera(href, s["url"])
                if sokvag and sokvag not in kanda:
                    okanda[sokvag] = None
        # Kontrollera länkar som inte pekar på en känd sida: verkligen trasiga (404/410) eller bara okända.
        sem = asyncio.Semaphore(5)

        async def kontroll(sokvag):
            async with sem:
                okanda[sokvag] = await h.status(sokvag.lstrip("/"))

        await asyncio.gather(*(kontroll(v) for v in list(okanda)[:MAX_LANKKONTROLLER]))

    with db._conn() as c:
        # Befintliga sidor behåller sitt id (uppdateras på adress), så att val och länkar i gränssnittet består.
        befintliga = {r["url"]: r["id"] for r in c.execute("SELECT id, url FROM sajt_sidor")}
        nya_urler = {s["url"] for s in sidor}
        for url, gammalt_id in befintliga.items():
            if url not in nya_urler:
                c.execute("DELETE FROM sajt_sidor WHERE id = ?", (gammalt_id,))
        c.execute("DELETE FROM sajt_lankar")
        id_for: dict[str, int] = {}
        sedda: set[str] = set()
        for s in sidor:
            if s["url"] in sedda:
                continue
            sedda.add(s["url"])
            varden = (s["wp_id"], s["typ"], s["sokvag"], s["titel"], s["text"], json.dumps(s["rubriker"], ensure_ascii=False),
                      json.dumps(s["kategorier"]), s["seo_titel"], s["seo_beskrivning"], s["ord"], s["andrad"],
                      json.dumps(s["stycken"], ensure_ascii=False), s["fokus"])
            if s["url"] in befintliga:
                s["id"] = befintliga[s["url"]]
                c.execute("UPDATE sajt_sidor SET wp_id = ?, typ = ?, sokvag = ?, titel = ?, text = ?, rubriker = ?, kategorier = ?, "
                          "seo_titel = ?, seo_beskrivning = ?, ord = ?, andrad = ?, stycken = ?, fokus = ? WHERE id = ?",
                          (*varden, s["id"]))
            else:
                s["id"] = c.execute(
                    "INSERT INTO sajt_sidor (wp_id, typ, sokvag, titel, text, rubriker, kategorier, seo_titel, seo_beskrivning, "
                    "ord, andrad, stycken, fokus, url) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (*varden, s["url"])).lastrowid
            id_for[s["sokvag"]] = s["id"]
        antal_lankar = 0
        for s in sidor:
            if "id" not in s:
                continue
            for href, ankare in s["_lankar"]:
                sokvag = normalisera(href, s["url"])
                if not sokvag or sokvag == s["sokvag"]:
                    continue
                if sokvag in id_for:
                    status = "ok"
                else:
                    kod = okanda.get(sokvag)
                    status = "trasig" if kod in (404, 410) else "okand"
                c.execute("INSERT INTO sajt_lankar (fran_id, till_url, till_id, ankartext, status) VALUES (?, ?, ?, ?, ?)",
                          (s["id"], sokvag, id_for.get(sokvag), ankare[:200], status))
                antal_lankar += 1
        c.execute("INSERT INTO installningar (nyckel, varde) VALUES ('kartlagd', ?) "
                  "ON CONFLICT(nyckel) DO UPDATE SET varde = excluded.varde", (db.nu(),))
    log.info("Sajten kartlagd: %s sidor, %s interna länkar", len(id_for), antal_lankar)
    return {"sidor": len(id_for), "lankar": antal_lankar}


# ---------- Analys ----------

def _ladda() -> tuple[list[dict], list[dict], str | None, set[str]]:
    with db._conn() as c:
        sidor = [dict(r) for r in c.execute("SELECT * FROM sajt_sidor")]
        lankar = [dict(r) for r in c.execute("SELECT * FROM sajt_lankar")]
        r = c.execute("SELECT varde FROM installningar WHERE nyckel = 'kartlagd'").fetchone()
        kommersiella = {r2[0] for r2 in c.execute("SELECT url FROM sajt_markningar WHERE kommersiell = 1")}
    for s in sidor:
        s["rubriker"] = json.loads(s["rubriker"])
        s["kategorier"] = json.loads(s["kategorier"])
    return sidor, lankar, (r[0] if r else None), kommersiella


def _amne(s: dict) -> str:
    return f"{s['titel']} {s['sokvag'].replace('-', ' ').replace('/', ' ')}"


def analysera() -> dict:
    sidor, lankar, kartlagd, kommersiella = _ladda()
    if not sidor:
        return {"kartlagd": None, "noder": [], "kanter": [], "kluster": [], "verifierat": {}, "beraknat": {}}

    per_id = {s["id"]: s for s in sidor}
    kategori_nod = {s["wp_id"]: s for s in sidor if s["typ"] == "kategori"}
    in_fran: dict[int, set[int]] = defaultdict(set)
    ut_till: dict[int, set[int]] = defaultdict(set)
    trasiga: dict[int, list[dict]] = defaultdict(list)
    for l in lankar:
        if l["status"] == "ok" and l["till_id"]:
            in_fran[l["till_id"]].add(l["fran_id"])
            ut_till[l["fran_id"]].add(l["till_id"])
        elif l["status"] == "trasig":
            trasiga[l["fran_id"]].append({"url": l["till_url"], "ankartext": l["ankartext"]})

    # Kluster: artiklarnas första kategori (verifierat). Sidor placeras efter textlikhet (beräknat).
    kluster_for: dict[int, str] = {}
    kluster_namn: dict[str, str] = {}
    for kid, k in kategori_nod.items():
        kluster_namn[f"k{kid}"] = k["titel"]
    for s in sidor:
        if s["typ"] == "post" and s["kategorier"]:
            kluster_for[s["id"]] = f"k{s['kategorier'][0]}"
        elif s["typ"] == "kategori":
            kluster_for[s["id"]] = f"k{s['wp_id']}"
    kluster_text: dict[str, str] = defaultdict(str)
    for s in sidor:
        if s["id"] in kluster_for:
            kluster_text[kluster_for[s["id"]]] += " " + _amne(s)
    for s in sidor:
        if s["id"] not in kluster_for:
            bast, poang = None, 0.0
            for kid, text in kluster_text.items():
                t = _token(text)
                p = len(_token(_amne(s)) & t) / max(1, len(_token(_amne(s))))
                if p > poang:
                    bast, poang = kid, p
            kluster_for[s["id"]] = bast if bast and poang >= 0.34 else "ovrigt"
    kluster_namn.setdefault("ovrigt", "Övriga sidor")

    # Pelarsida per kluster: flest inkommande länkar från klustret, sedan längd. Kategorisidor räknas inte.
    medlemmar: dict[str, list[int]] = defaultdict(list)
    for sid, kid in kluster_for.items():
        medlemmar[kid].append(sid)
    pelare: dict[str, int] = {}
    for kid, ids in medlemmar.items():
        kandidater = [i for i in ids if per_id[i]["typ"] != "kategori"]
        if len(kandidater) < 3 or kid == "ovrigt":
            continue

        def poang(i):
            inom = len(in_fran[i] & set(ids))
            return (inom * 3 + len(ut_till[i] & set(ids)) + per_id[i]["ord"] / 800, per_id[i]["typ"] == "page")

        bast = max(kandidater, key=poang)
        if len(in_fran[bast] & set(ids)) >= 1 or per_id[bast]["typ"] == "page":
            pelare[kid] = bast

    noder = []
    for s in sidor:
        sid = s["id"]
        kid = kluster_for[sid]
        foraldralos = s["typ"] != "kategori" and not (in_fran[sid] - {sid})
        isolerad = foraldralos and not ut_till[sid]
        roll = "kategori" if s["typ"] == "kategori" else "pelare" if pelare.get(kid) == sid else "stod"
        noder.append({
            "id": sid, "titel": s["titel"], "url": s["url"], "typ": s["typ"], "kluster": kid, "roll": roll,
            "in": len(in_fran[sid]), "ut": len(ut_till[sid]), "trasiga": len(trasiga[sid]), "foraldralos": foraldralos,
            "isolerad": isolerad, "fa_inkommande": s["typ"] != "kategori" and 1 <= len(in_fran[sid]) <= 2,
            "ord": s["ord"], "seo_titel": s["seo_titel"], "seo_beskrivning": s["seo_beskrivning"],
            "kommersiell": s["url"] in kommersiella,
        })
    kanter = [{"fran": l["fran_id"], "till": l["till_id"]} for l in lankar if l["status"] == "ok" and l["till_id"]]
    kanter = [dict(t) for t in {tuple(k.items()) for k in kanter}]

    innehall = [s for s in sidor if s["typ"] in ("post", "page")]
    verifierat = {
        "trasiga_lankar": [{"id": sid, "titel": per_id[sid]["titel"], "lankar": l} for sid, l in trasiga.items()],
        "utan_inkommande": [{"id": n["id"], "titel": n["titel"]} for n in noder if n["foraldralos"]],
        "isolerade": [{"id": n["id"], "titel": n["titel"]} for n in noder if n["isolerad"]],
        "fa_inkommande": [{"id": n["id"], "titel": n["titel"], "in": n["in"]} for n in noder if n["fa_inkommande"]],
        "saknar_metabeskrivning": [{"id": s["id"], "titel": s["titel"]} for s in innehall
                                   if s["seo_beskrivning"] is not None and not s["seo_beskrivning"].strip()],
    }

    # Beräknat: möjlig kannibalisering och närliggande artiklar som inte länkar till varandra.
    kannibal, saknade = [], []
    for i, a in enumerate(innehall):
        for b in innehall[i + 1:]:
            p = likhet(a["titel"], b["titel"])
            if p >= 0.75:
                kannibal.append({"a": a["id"], "b": b["id"], "a_titel": a["titel"], "b_titel": b["titel"], "likhet": round(p, 2)})
            elif (p >= 0.34 and kluster_for[a["id"]] == kluster_for[b["id"]]
                  and b["id"] not in ut_till[a["id"]] and a["id"] not in ut_till[b["id"]]):
                saknade.append({"a": a["id"], "b": b["id"], "a_titel": a["titel"], "b_titel": b["titel"], "likhet": round(p, 2)})
    saknade.sort(key=lambda x: -x["likhet"])
    kommersiella_noder = [n for n in noder if n["kommersiell"]]
    beraknat = {
        "mojlig_kannibalisering": kannibal,
        "saknade_lankar": saknade[:40],
        "svaga_kommersiella": [{"id": n["id"], "titel": n["titel"], "in": n["in"]} for n in kommersiella_noder if n["in"] < 3],
    }

    kluster = []
    for kid, ids in sorted(medlemmar.items(), key=lambda x: -len(x[1])):
        kluster.append({"id": kid, "namn": kluster_namn.get(kid, kid), "antal": len(ids), "pelare": pelare.get(kid)})
    return {"kartlagd": kartlagd, "noder": noder, "kanter": kanter, "kluster": kluster,
            "verifierat": verifierat, "beraknat": beraknat}


def nod_detaljer(sid: int) -> dict | None:
    from .lankforslag import for_sida, huvudnyckelord

    with db._conn() as c:
        s = c.execute("SELECT id, wp_id, typ, url, titel, rubriker, seo_titel, seo_beskrivning, ord, fokus FROM sajt_sidor WHERE id = ?",
                      (sid,)).fetchone()
        if not s:
            return None
        ut = [dict(r) for r in c.execute(
            "SELECT l.till_url, l.till_id, l.ankartext, l.status, s.titel FROM sajt_lankar l "
            "LEFT JOIN sajt_sidor s ON s.id = l.till_id WHERE l.fran_id = ?", (sid,))]
        inn = [dict(r) for r in c.execute(
            "SELECT l.fran_id, l.ankartext, s.titel, s.url FROM sajt_lankar l JOIN sajt_sidor s ON s.id = l.fran_id "
            "WHERE l.till_id = ? AND l.fran_id != ?", (sid, sid))]
    d = dict(s)
    d["rubriker"] = json.loads(d["rubriker"])
    d["huvudnyckelord"], d["nyckelord_kalla"] = huvudnyckelord(d)
    # Rekommendationer: väntande länkförslag där sidan är källa eller mål, och närliggande sidor utan länk.
    forslag = for_sida(sid)
    analys = analysera()
    narliggande = [x for x in analys["beraknat"]["saknade_lankar"] if sid in (x["a"], x["b"])][:5]
    return {**d, "utgaende": ut, "inkommande": inn, "forslag": forslag, "narliggande": narliggande}


def satt_kommersiell(sid: int, kommersiell: bool) -> None:
    with db._conn() as c:
        r = c.execute("SELECT url FROM sajt_sidor WHERE id = ?", (sid,)).fetchone()
        if not r:
            raise KartFel("Sidan finns inte i kartan.")
        c.execute("INSERT INTO sajt_markningar (url, kommersiell) VALUES (?, ?) "
                  "ON CONFLICT(url) DO UPDATE SET kommersiell = excluded.kommersiell", (r[0], int(kommersiell)))


def lankmal(huvudsokord: str, sekundara: list[str], antal: int = 10) -> list[dict]:
    """Befintliga sidor som passar som internlänkar i en ny artikel, mest relevanta först.

    Bara sidor som finns i den senaste kartläggningen kan komma med.
    """
    sidor, _, _, kommersiella = _ladda()
    fraga = _token(" ".join([huvudsokord, *sekundara]))
    if not fraga:
        return []
    poang = []
    for s in sidor:
        t = _token(_amne(s) + " " + " ".join(s["rubriker"][:8]))
        traff = len(fraga & t)
        if traff:
            vikt = traff / len(fraga) + (0.15 if s["url"] in kommersiella else 0) + (0.1 if s["typ"] == "kategori" else 0)
            poang.append((vikt, s))
    poang.sort(key=lambda x: -x[0])
    return [{"url": s["url"], "titel": s["titel"], "typ": s["typ"]} for _, s in poang[:antal]]


def kanda_urler() -> set[str]:
    with db._conn() as c:
        return {r[0] for r in c.execute("SELECT url FROM sajt_sidor")}
