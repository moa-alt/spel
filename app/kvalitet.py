"""Automatisk kvalitetskontroll före godkännande, schemaläggning och publicering.

Resultatet delas i kritiska fel, som blockerar, och varningar, som du bedömer själv.
Faktapåståenden som behöver verifieras flaggas och måste bekräftas manuellt vid godkännandet.
Ett högt SEO-resultat godkänner aldrig en artikel; godkännandet är alltid ditt eget beslut.
"""

import json
import re
from html import unescape
from urllib.parse import urlsplit

from . import bilder, config, db, sajtkarta
from .demo import slugify
from .planering import likhet, normalisera_sokord
from .render import BILD_MARKOR
from .seo_ai import _HREF, _LANK

MAX_FAKTA = 15
GRANS_KANNIBAL = 0.75
SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# Meningar med siffror och enheter, eller hänvisningar till regler, myndigheter och studier, ska kontrolleras.
FAKTA_ENHET = re.compile(r"\d[\d\s,.–-]*\s?(kr|kronor|sek|%|procent|kwh|mwh|kw|watt|grader|°c?|år|månader|m²|m2|kvm|db|liter|ton|timmar)\b", re.I)
FAKTA_ORD = re.compile(
    r"\b(studie|studier|forskning|forskare|undersökning|statistik|enligt|lagen|lagkrav|krav|regler|regel|förordning|"
    r"boverket|skatteverket|energimyndigheten|naturvårdsverket|bbr|rot-?avdrag|rut-?avdrag|bidrag|stöd|garanti|"
    r"certifier\w*|testvinnare|bäst i test|vetenskap\w*|bevisat|sparar upp till)\b", re.I)
# Hur länge en bild får vara utan att räknas som tung (kB) och minsta bredd för en skarp bild på stora skärmar.
MAX_BILD_KB = 400
MIN_BILD_BREDD = 1200
SLUTSTATUS_MAL = ("godkand", "schemalagd", "publicerad")


def _text(html_: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html_ or ""))).strip()


def _punkt(kod: str, text: str) -> dict:
    return {"kod": kod, "text": text}


# ---------- Delkontroller ----------

def _sokord_och_meta(inn: dict, kw: str, k: list, v: list, ok: list) -> None:
    kwl = kw.lower()
    titel, mt, md, slug = inn.get("titel", ""), inn.get("meta_titel", ""), inn.get("meta_beskrivning", ""), inn.get("slug", "")
    if not titel.strip():
        k.append(_punkt("titel_saknas", "Rubriken (H1) saknas."))
    elif kwl not in titel.lower():
        k.append(_punkt("sokord_titel", f"Huvudsökordet ”{kw}” finns inte i rubriken (H1)."))
    else:
        ok.append("Huvudsökordet finns i rubriken")
    if not mt.strip():
        k.append(_punkt("metatitel_saknas", "SEO-titeln (metatitel) saknas."))
    else:
        if kwl not in mt.lower():
            k.append(_punkt("sokord_metatitel", f"Huvudsökordet ”{kw}” finns inte i SEO-titeln."))
        if len(mt) > 60:
            v.append(_punkt("metatitel_lang", f"SEO-titeln är {len(mt)} tecken och kan kortas av i Google (rek. högst 60)."))
        elif len(mt) < 30:
            v.append(_punkt("metatitel_kort", f"SEO-titeln är bara {len(mt)} tecken (rek. 30–60)."))
        else:
            ok.append("SEO-titeln har bra längd")
    if not md.strip():
        k.append(_punkt("metabeskrivning_saknas", "Metabeskrivningen saknas."))
    else:
        if kwl not in md.lower():
            v.append(_punkt("sokord_metabeskrivning", "Huvudsökordet finns inte i metabeskrivningen."))
        if len(md) > 160:
            v.append(_punkt("metabeskrivning_lang", f"Metabeskrivningen är {len(md)} tecken (rek. högst 155)."))
        elif len(md) < 110:
            v.append(_punkt("metabeskrivning_kort", f"Metabeskrivningen är bara {len(md)} tecken (rek. 120–155)."))
        else:
            ok.append("Metabeskrivningen har bra längd")
    if not SLUG.match(slug or ""):
        k.append(_punkt("slug_ogiltig", "URL-slugen saknas eller innehåller otillåtna tecken."))
    elif not all(d in slug for d in slugify(kw).split("-") if d):
        v.append(_punkt("slug_sokord", "URL-slugen innehåller inte hela huvudsökordet."))
    forsta = _text((re.search(r"<p[\s>].*?</p>", inn.get("html", ""), re.S | re.I) or [""])[0]).lower()
    if kwl not in forsta:
        v.append(_punkt("sokord_ingress", "Huvudsökordet nämns inte i första stycket."))


def _struktur(inn: dict, k: list, v: list, ok: list) -> None:
    body = inn.get("html", "")
    if re.search(r"<h1[\s>]", body, re.I):
        k.append(_punkt("h1_i_text", "Brödtexten innehåller en H1. Rubriken (H1) sätts av WordPress; använd H2 i texten."))
    h2 = re.findall(r"<h2([^>]*)>(.*?)</h2>", body, re.S | re.I)
    if len(h2) < 2:
        k.append(_punkt("for_fa_h2", f"Artikeln har {len(h2)} underrubriker (H2). Minst två krävs för en begriplig struktur."))
    elif len(h2) < 3:
        v.append(_punkt("fa_h2", "Artikeln har bara två underrubriker (H2)."))
    else:
        ok.append(f"{len(h2)} underrubriker (H2)")
    if any(not _text(t) for _, t in h2):
        k.append(_punkt("tom_rubrik", "En underrubrik är tom."))
    ids = [m.group(1) for a, _ in h2 if (m := re.search(r'id="([^"]+)"', a))]
    if len(ids) < len(h2):
        v.append(_punkt("h2_utan_id", "Alla H2 har inte ett id; innehållsförteckningen kan inte länka till dem."))
    if len(set(ids)) < len(ids):
        v.append(_punkt("h2_dubbla_id", "Två underrubriker har samma id."))
    forsta_h2 = re.search(r"<h2[\s>]", body, re.I)
    forsta_h3 = re.search(r"<h3[\s>]", body, re.I)
    if forsta_h3 and (not forsta_h2 or forsta_h3.start() < forsta_h2.start()):
        v.append(_punkt("h3_fore_h2", "En H3 står före första H2; rubriknivåerna hoppar."))
    si = inn.get("sokintention")
    if not si:
        v.append(_punkt("sokintention_saknas", "Sökintentionen är inte analyserad. Kontrollera själv att texten besvarar det läsaren söker."))
    else:
        ok.append(f"Sökintention: {si.get('typ', '')}")
        if si.get("typ") in ("kommersiell", "transaktionell") and not re.search(r"kostnad|pris|kr\b|jämför|för- och nackdelar|kh-procon|kh-tabell", body, re.I):
            v.append(_punkt("intention_matchar", "Sökintentionen är kommersiell men texten saknar priser, jämförelser eller för- och nackdelar."))
    ord_ = len(_text(body).split())
    if ord_ < 600:
        v.append(_punkt("kort_text", f"Texten är {ord_} ord. Konkurrerande guider är ofta betydligt längre."))


def _bilder(artikel: dict, inn: dict, k: list, v: list, ok: list) -> None:
    alla = {b["id"]: b for b in inn.get("bilder", [])}
    placerade = [int(x) for x in BILD_MARKOR.findall(inn.get("html", ""))]
    for bid in placerade:
        b = alla.get(bid)
        if b is None:
            k.append(_punkt("bild_okand", f"Bildmarkören för bild {bid} saknar bild."))
            continue
        if b.get("status") == "genereras":
            k.append(_punkt("bild_genereras", f"Bild {bid} håller på att skapas."))
            continue
        if not b.get("fil"):
            v.append(_punkt("bild_saknas", f"Bild {bid} är inte skapad och tas inte med i inlägget."))
            continue
        fil = bilder.sokvag(artikel["id"], b["fil"])
        if fil is None or not fil.is_file():
            k.append(_punkt("bildfil_saknas", f"Bildfilen för bild {bid} saknas på servern. Skapa eller byt bilden."))
        if b.get("kalla") == "test":
            k.append(_punkt("testbild", f"Bild {bid} är en testbild och kan inte publiceras."))
        alt = (b.get("alt") or "").strip()
        if len(alt) < 10:
            k.append(_punkt("alt_saknas", f"Bild {bid} saknar en beskrivande alt-text."))
        elif varning := bilder.alt_kontroll(alt):
            v.append(_punkt("alt_varning", f"Bild {bid}: {varning}"))
        if (b.get("bredd") or 0) < MIN_BILD_BREDD:
            v.append(_punkt("bild_liten", f"Bild {bid} är {b.get('bredd')} px bred och kan bli suddig på stora skärmar (rek. minst {MIN_BILD_BREDD} px)."))
        if (b.get("storlek_kb") or 0) > MAX_BILD_KB:
            v.append(_punkt("bild_tung", f"Bild {bid} är {b.get('storlek_kb')} kB (rek. högst {MAX_BILD_KB} kB)."))
    klara = [bid for bid in placerade if alla.get(bid, {}).get("fil")]
    if 1 not in klara:
        v.append(_punkt("ingen_huvudbild", "Huvudbilden saknas, så inlägget får ingen utvald bild."))
    if klara:
        ok.append(f"{len(klara)} av {len(placerade)} bilder klara som WebP")


def _artikel_mal() -> dict[str, dict]:
    """Sökväg → artikel i kön (inte avvisad), med låst adress före innehållets slug."""
    ut: dict[str, dict] = {}
    with db._conn() as c:
        for r in c.execute("SELECT id, status, innehall, mal_slug, wp_lank FROM artiklar WHERE status != 'avvisad'"):
            slug = r["mal_slug"] or (json.loads(r["innehall"]).get("slug") if r["innehall"] else None)
            if slug:
                ut.setdefault(f"/{slug}/", {"id": r["id"], "status": r["status"], "wp_lank": r["wp_lank"]})
    return ut


def _planerade_mal() -> set[str]:
    """Adresser som du uttryckligen markerat som planerade (Saknade sidor)."""
    with db._conn() as c:
        try:
            return {r[0] for r in c.execute("SELECT sokvag FROM planerade_mal WHERE status = 'planerad'")}
        except Exception:
            return set()


def lankar(artikel: dict) -> list[dict]:
    """Varje intern länk med sin klass: publicerad, planerad (godkänt framtida mål), ej_godkand, trasig eller okand."""
    bas = config.settings.wp_url.rstrip("/")
    vard = urlsplit(bas).netloc if bas else ""
    kanda = sajtkarta.kanda_urler()
    kanda_vagar = {urlsplit(u).path.rstrip("/") + "/" for u in kanda}
    mal = _artikel_mal()
    planerade = _planerade_mal()
    egen = f"/{artikel.get('mal_slug') or (artikel.get('innehall') or {}).get('slug', '')}/"
    ut = []
    for m in _LANK.finditer((artikel.get("innehall") or {}).get("html", "")):
        href = _HREF.search(m.group(1))
        url = unescape(href.group(1)).strip() if href else ""
        if not url or url.startswith("#") or url.startswith("mailto:"):
            continue
        delar = urlsplit(url)
        intern = url.startswith("/") or (vard and delar.netloc == vard)
        if not intern:
            continue
        vag = (delar.path or "/").rstrip("/") + "/"
        ankare = " ".join(_text(m.group(2)).split())
        a = mal.get(vag)
        if vag == egen:
            klass = "egen"
        elif vag in kanda_vagar:
            klass = "publicerad"
        elif a and a["status"] == "publicerad":
            klass = "publicerad"
        elif (a and a["status"] in SLUTSTATUS_MAL) or vag in planerade:
            klass = "planerad"
        elif a:
            klass = "ej_godkand"
        elif not kanda:
            klass = "okand"
        else:
            klass = "trasig"
        ut.append({"url": url, "sokvag": vag, "ankartext": ankare, "klass": klass})
    return ut


def _lankar(artikel: dict, k: list, v: list, ok: list) -> None:
    alla = lankar(artikel)
    for l in alla:
        if l["klass"] == "trasig":
            k.append(_punkt("trasig_lank", f"Länken ”{l['ankartext']}” går till {l['sokvag']}, som inte finns på sajten och inte är ett godkänt framtida mål."))
        elif l["klass"] == "ej_godkand":
            k.append(_punkt("lank_ej_godkand", f"Länken ”{l['ankartext']}” går till {l['sokvag']}, en artikel som ännu inte är godkänd."))
        elif l["klass"] == "planerad":
            v.append(_punkt("lank_planerad", f"Länken till {l['sokvag']} går till ett godkänt framtida mål. Är sidan inte publicerad när artikeln publiceras, blir länken vanlig text."))
        elif l["klass"] == "okand":
            v.append(_punkt("lank_okand", f"Länken till {l['sokvag']} kan inte kontrolleras eftersom sajten inte är kartlagd."))
    publicerade = [l for l in alla if l["klass"] == "publicerad"]
    if publicerade:
        ok.append(f"{len(publicerade)} interna länkar till publicerade sidor")
    elif not alla:
        v.append(_punkt("inga_lankar", "Artikeln har inga interna länkar."))


def _kannibalisering(artikel: dict, k: list, v: list) -> None:
    kw = normalisera_sokord(artikel["huvudsokord"]).lower()
    egen_url = artikel.get("wp_lank") or ""
    egen_vag = f"/{artikel.get('mal_slug') or (artikel.get('innehall') or {}).get('slug', '')}/"
    with db._conn() as c:
        sidor = [dict(r) for r in c.execute("SELECT url, sokvag, titel, fokus, typ FROM sajt_sidor WHERE typ IN ('post', 'page')")]
        andra = [dict(r) for r in c.execute("SELECT id, huvudsokord, status FROM artiklar WHERE status != 'avvisad' AND id != ?",
                                            (artikel["id"],))]
    for s in sidor:
        if s["url"] == egen_url:
            continue
        if s["sokvag"] == egen_vag:
            k.append(_punkt("slug_upptagen", f"Adressen {egen_vag} används redan av ”{s['titel']}” på sajten. Välj en annan slug."))
            continue
        fokus = normalisera_sokord(s.get("fokus") or "").lower()
        if fokus and fokus == kw:
            k.append(_punkt("kannibal_fokus", f"Sidan ”{s['titel']}” ({s['sokvag']}) har redan ”{kw}” som fokusnyckelord. Välj ett annat huvudsökord eller uppdatera den sidan i stället."))
        elif normalisera_sokord(s["titel"]).lower() == kw or (fokus and likhet(kw, fokus) >= GRANS_KANNIBAL):
            v.append(_punkt("kannibal_lik", f"Sidan ”{s['titel']}” ({s['sokvag']}) riktar sig troligen mot samma sökning."))
    for a in andra:
        annan = normalisera_sokord(a["huvudsokord"]).lower()
        if annan == kw:
            k.append(_punkt("kannibal_ko", f"En annan artikel i kön har samma huvudsökord (”{a['huvudsokord']}”)."))
        elif likhet(kw, annan) >= GRANS_KANNIBAL:
            v.append(_punkt("kannibal_ko_lik", f"Artikeln ”{a['huvudsokord']}” i kön har ett mycket likt huvudsökord."))


def fakta(inn: dict) -> list[str]:
    """Meningar med siffror, regler, myndigheter eller studier som en människa bör verifiera."""
    body = re.sub(r"<(h2|h3|summary)[^>]*>.*?</\1>", " ", inn.get("html", ""), flags=re.S | re.I)
    meningar = re.split(r"(?<=[.!?])\s+|\s*</?(?:p|li|td|th)[^>]*>\s*", body)
    ut: list[str] = []
    for m in meningar:
        t = _text(m)
        if len(t) < 15 or t in ut:
            continue
        if FAKTA_ENHET.search(t) or FAKTA_ORD.search(t):
            ut.append(t[:240])
        if len(ut) >= MAX_FAKTA:
            break
    return ut


# ---------- Samlad kontroll ----------

def kontrollera(artikel: dict) -> dict:
    inn = artikel.get("innehall")
    if not inn:
        return {"kritiska": [_punkt("inget_innehall", "Artikeln har ingen text ännu.")], "varningar": [], "ok": [], "fakta": [],
                "kan_godkannas": False}
    k: list[dict] = []
    v: list[dict] = []
    ok: list[str] = []
    _sokord_och_meta(inn, artikel["huvudsokord"], k, v, ok)
    _struktur(inn, k, v, ok)
    _bilder(artikel, inn, k, v, ok)
    _lankar(artikel, k, v, ok)
    _kannibalisering(artikel, k, v)
    if config.settings.wp_url and inn.get("kategori_id") is None:
        v.append(_punkt("ingen_kategori", "Ingen kategori är vald. WordPress lägger då inlägget i standardkategorin."))
    elif inn.get("kategori_id") is not None and (kategorier := wp_kategorier()) and inn["kategori_id"] not in {x["id"] for x in kategorier}:
        k.append(_punkt("okand_kategori", "Den valda kategorin finns inte längre på sajten."))
    return {"kritiska": k, "varningar": v, "ok": ok, "fakta": fakta(inn), "kan_godkannas": not k}


def sammanfattning(resultat: dict) -> str:
    return " ".join(p["text"] for p in resultat["kritiska"][:3]) + (
        f" (+{len(resultat['kritiska']) - 3} till)" if len(resultat["kritiska"]) > 3 else "")


def ej_publicerade_mal(artikel: dict) -> set[str]:
    """Länkar till godkända framtida mål som ännu inte publicerats; de blir vanlig text vid publicering."""
    return {l["url"] for l in lankar(artikel) if l["klass"] == "planerad"}


def wp_kategorier() -> list[dict]:
    """Kategorierna från den senaste kartläggningen av sajten."""
    with db._conn() as c:
        return [{"id": r[0], "namn": r[1]} for r in
                c.execute("SELECT wp_id, titel FROM sajt_sidor WHERE typ = 'kategori' AND wp_id IS NOT NULL ORDER BY titel")]
