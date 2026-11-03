"""Designsystemet för artiklar: strukturerat innehåll och en deterministisk HTML-renderare.

Artikeln lagras som ett dokument (innehall["dokument"]) med hero, introduktion, sektioner,
sammanfattning, vanliga frågor och relaterade artiklar. Varje sektion har en layout och en lista
med innehållsblock. AI:n väljer layout och block efter innehållet; presentationen bestäms helt här
och i static/design.css, så att förhandsgranskningen och WordPress-exporten blir identiska.

Säkerhet: all text escapas. Fält med formatering får bara innehålla strong, em, br och säkra
länkar (sanitize.rensa_inline). Samma dokument ger alltid exakt samma HTML.

Äldre artiklar utan dokument (rå HTML med kh-klasser) renderas som förut av render.py.
"""

import html
import json
import re

from .demo import slugify
from .sanitize import rensa_inline

FORMAT = 2
LAYOUTER = ("standard", "mork", "bild", "losningar")
BILDLAYOUTER = ("mork", "bild")
BLOCKTYPER = ("stycke", "underrubrik", "lista", "checklista", "steg", "notis", "kort", "tabell")
RESERVERADE_ID = {"innehall", "vanliga-fragor", "sammanfattning", "relaterade"}
MAX_SEKTIONER = 14
MAX_BLOCK = 30
MAX_PUNKTER = 12
MAX_STYCKEN = 10


def ar_strukturerad(innehall: dict | None) -> bool:
    return bool(innehall and isinstance(innehall.get("dokument"), dict))


# ---------- Normalisering ----------

def _text(v, maxlangd: int = 300) -> str:
    """Ren text: inga taggar (innehållet i skript och liknande tas bort helt), blanktecken hopslagna."""
    t = re.sub(r"<[^>]*>", "", rensa_inline(str(v or "")[:maxlangd * 4]))
    return " ".join(html.unescape(t).split())[:maxlangd]


def _rik(v, maxlangd: int = 4000) -> str:
    """Text med tillåten formatering (strong, em, br, länkar)."""
    return rensa_inline(str(v or "")[:maxlangd * 2])[:maxlangd]


def _lista(v) -> list:
    return v if isinstance(v, list) else []


def _post(p, rubrik="rubrik", text="text") -> dict | None:
    if not isinstance(p, dict):
        return None
    r, t = _text(p.get(rubrik), 160), _rik(p.get(text), 1500)
    return {rubrik: r, text: t} if (r or t) else None


def _block(b) -> dict | None:
    if not isinstance(b, dict) or b.get("typ") not in BLOCKTYPER:
        return None
    typ = b["typ"]
    if typ == "stycke":
        t = _rik(b.get("text"))
        return {"typ": typ, "text": t} if t else None
    if typ == "underrubrik":
        t = _text(b.get("text"), 160)
        return {"typ": typ, "text": t} if t else None
    if typ in ("lista", "checklista"):
        punkter = []
        for p in _lista(b.get("punkter"))[:MAX_PUNKTER]:
            t = _rik(p.get("text") if isinstance(p, dict) else p, 1000)
            if t:
                punkter.append(t)
        return {"typ": typ, "punkter": punkter} if punkter else None
    if typ == "steg":
        punkter = [p for p in (_post(p) for p in _lista(b.get("punkter"))[:MAX_PUNKTER]) if p]
        return {"typ": typ, "punkter": punkter} if punkter else None
    if typ == "notis":
        t = _rik(b.get("text"), 1500)
        return {"typ": typ, "etikett": _text(b.get("etikett"), 40), "text": t} if t else None
    if typ == "kort":
        punkter = [p for p in (_post(p) for p in _lista(b.get("punkter"))[:9]) if p]
        variant = b.get("variant") if b.get("variant") in ("problem", "losning") else "problem"
        return {"typ": typ, "variant": variant, "punkter": punkter} if punkter else None
    if typ == "tabell":
        kolumner = [_text(k, 80) for k in _lista(b.get("kolumner"))[:5]]
        kolumner = [k for k in kolumner if k]
        if len(kolumner) < 2:
            return None
        rader = []
        for rad in _lista(b.get("rader"))[:MAX_PUNKTER]:
            celler = [_rik(c, 500) for c in _lista(rad)][:len(kolumner)]
            if any(celler):
                rader.append(celler + [""] * (len(kolumner) - len(celler)))
        return {"typ": typ, "kolumner": kolumner, "rader": rader} if rader else None
    return None


def _unikt_id(onskat: str, upptagna: set[str]) -> str:
    bas = slugify(onskat)[:60].strip("-") or "avsnitt"
    if bas in RESERVERADE_ID:
        bas = f"{bas}-avsnitt"
    kandidat, n = bas, 2
    while kandidat in upptagna:
        kandidat, n = f"{bas}-{n}", n + 1
    upptagna.add(kandidat)
    return kandidat


def normalisera(dok: dict | None, fordela_bilder: bool = False) -> dict:
    """Ett giltigt dokument av vad som helst. Idempotent: ett normaliserat dokument ändras inte.

    fordela_bilder: placera bild 2–4 i lämpliga sektioner om AI:n inte gjort det (bara vid generering).
    """
    dok = dok if isinstance(dok, dict) else {}
    hero = dok.get("hero") if isinstance(dok.get("hero"), dict) else {}
    intro = dok.get("intro") if isinstance(dok.get("intro"), dict) else {}
    upptagna: set[str] = set()

    ut_intro = {
        "etikett": _text(intro.get("etikett"), 60),
        "rubrik": _text(intro.get("rubrik"), 160),
        "stycken": [t for t in (_rik(s) for s in _lista(intro.get("stycken"))[:MAX_STYCKEN]) if t],
        "fakta": None,
    }
    if ut_intro["rubrik"]:
        ut_intro["id"] = _unikt_id(intro.get("id") or ut_intro["rubrik"], upptagna)
    fakta = intro.get("fakta") if isinstance(intro.get("fakta"), dict) else None
    if fakta:
        punkter = [p for p in (_post(p, "titel", "text") for p in _lista(fakta.get("punkter"))[:6]) if p]
        if punkter:
            ut_intro["fakta"] = {"etikett": _text(fakta.get("etikett"), 40) or "Snabböversikt",
                                 "rubrik": _text(fakta.get("rubrik"), 120), "punkter": punkter}

    sektioner = []
    anvanda_bilder: set[int] = set()
    for s in _lista(dok.get("sektioner"))[:MAX_SEKTIONER]:
        if not isinstance(s, dict):
            continue
        rubrik = _text(s.get("rubrik"), 160)
        block = [b for b in (_block(b) for b in _lista(s.get("block"))[:MAX_BLOCK]) if b]
        if not rubrik or not (block or s.get("ingress")):
            continue
        layout = s.get("layout") if s.get("layout") in LAYOUTER else "standard"
        bild = s.get("bild") if isinstance(s.get("bild"), int) and not isinstance(s.get("bild"), bool) else None
        if bild not in (2, 3, 4) or bild in anvanda_bilder or layout not in BILDLAYOUTER:
            bild = None
        if bild:
            anvanda_bilder.add(bild)
        sektioner.append({
            "id": _unikt_id(s.get("id") or rubrik, upptagna), "layout": layout,
            "etikett": _text(s.get("etikett"), 60), "rubrik": rubrik, "meny": _text(s.get("meny"), 40),
            "ingress": _rik(s.get("ingress"), 1500), "bild": bild, "block": block,
        })
    if fordela_bilder:
        _fordela_bilder(sektioner, anvanda_bilder)

    ids = {s["id"] for s in sektioner}
    knapp = hero.get("knapp") if isinstance(hero.get("knapp"), dict) else None
    if knapp and (not _text(knapp.get("text")) or knapp.get("mal") not in ids):
        knapp = None
    ut = {
        "version": FORMAT,
        "hero": {"etikett": _text(hero.get("etikett"), 80), "ingress": _rik(hero.get("ingress"), 1500),
                 "knapp": {"text": _text(knapp.get("text"), 60), "mal": knapp["mal"]} if knapp else None},
        "intro": ut_intro,
        "sektioner": sektioner,
        "sammanfattning": None,
        "faq": None,
        "relaterade": [],
        "relaterade_rubrik": _text(dok.get("relaterade_rubrik"), 120),
    }
    sam = dok.get("sammanfattning") if isinstance(dok.get("sammanfattning"), dict) else None
    if sam:
        stycken = [t for t in (_rik(s) for s in _lista(sam.get("stycken"))[:MAX_STYCKEN]) if t]
        if stycken:
            ut["sammanfattning"] = {"etikett": _text(sam.get("etikett"), 60) or "Sammanfattning",
                                    "rubrik": _text(sam.get("rubrik"), 160) or "Sammanfattning", "stycken": stycken}
    faq = dok.get("faq") if isinstance(dok.get("faq"), dict) else None
    if faq:
        fragor = [p for p in (_post(p, "fraga", "svar") for p in _lista(faq.get("fragor"))[:10]) if p and p["fraga"]]
        if fragor:
            ut["faq"] = {"etikett": _text(faq.get("etikett"), 60) or "Vanliga frågor",
                         "rubrik": _text(faq.get("rubrik"), 160) or "Vanliga frågor", "fragor": fragor}
    for r in _lista(dok.get("relaterade"))[:4]:
        if isinstance(r, dict) and isinstance(r.get("url"), str) and re.match(r"^(https?://|/)", r["url"]) and _text(r.get("titel")):
            ut["relaterade"].append({"url": r["url"][:500], "titel": _text(r.get("titel"), 160), "etikett": _text(r.get("etikett"), 60)})
    return ut


def _fordela_bilder(sektioner: list[dict], anvanda: set[int]) -> None:
    """Placerar bild 2–4 i olika sektioner, helst sådana som redan har bildlayout och sprids över artikeln."""
    saknas = [b for b in (2, 3, 4) if b not in anvanda]
    if not saknas:
        return
    kandidater = [s for s in sektioner if s["layout"] in BILDLAYOUTER and not s["bild"]]
    # Därefter textsektioner utan kort och tabeller, som fungerar bra som text och bild.
    kandidater += [s for s in sektioner if s["layout"] == "standard"
                   and not any(b["typ"] in ("kort", "tabell") for b in s["block"])]
    if not kandidater:
        return
    for bild, s in zip(saknas, kandidater):
        if s["layout"] == "standard":
            s["layout"] = "bild"
        s["bild"] = bild


def bildplatser(dok: dict) -> list[int]:
    """Bilderna som dokumentet har plats för, i läsordning (bild 1 är alltid huvudbilden)."""
    return [1] + [s["bild"] for s in normalisera(dok)["sektioner"] if s["bild"]]


# ---------- Länkar och relaterade artiklar ----------

def textfalt(dok: dict) -> list[tuple[list, str | int]]:
    """Alla fält med formatering, som (behållare, nyckel), så att de kan läsas och skrivas."""
    ut: list[tuple] = []
    hero, intro = dok.get("hero") or {}, dok.get("intro") or {}
    if "ingress" in hero:
        ut.append((hero, "ingress"))
    ut += [(intro["stycken"], i) for i in range(len(intro.get("stycken", [])))]
    for p in (intro.get("fakta") or {}).get("punkter", []):
        ut.append((p, "text"))
    for s in dok.get("sektioner", []):
        ut.append((s, "ingress"))
        for b in s["block"]:
            if b["typ"] in ("stycke", "notis"):
                ut.append((b, "text"))
            elif b["typ"] in ("lista", "checklista"):
                ut += [(b["punkter"], i) for i in range(len(b["punkter"]))]
            elif b["typ"] in ("steg", "kort"):
                ut += [(p, "text") for p in b["punkter"]]
            elif b["typ"] == "tabell":
                ut += [(rad, i) for rad in b["rader"] for i in range(len(rad))]
    for i in range(len((dok.get("sammanfattning") or {}).get("stycken", []))):
        ut.append((dok["sammanfattning"]["stycken"], i))
    ut += [(f, "svar") for f in (dok.get("faq") or {}).get("fragor", [])]
    return ut


def rensa_lankar(dok: dict, tillatna: set[str]) -> list[dict]:
    """Behåller bara länkar till kända adresser, högst en per adress i hela artikeln. Returnerar länkarna."""
    from .seo_ai import kontrollera_lankar

    sedda: set[str] = set()
    lankar: list[dict] = []
    for behallare, nyckel in textfalt(dok):
        behallare[nyckel], _ = kontrollera_lankar(behallare[nyckel], tillatna, sedda, lankar)
    return lankar


def relaterade(lankmal: list[dict], internlankar: list[dict], antal: int = 4) -> list[dict]:
    """Relaterade artiklar från sajtkartan: först sidor som texten länkar till, sedan andra relevanta sidor."""
    from . import db

    ordning = [l["url"] for l in internlankar] + [m["url"] for m in lankmal]
    ut, sedda = [], set()
    with db._conn() as c:
        kategorier = {r[0]: r[1] for r in c.execute("SELECT wp_id, titel FROM sajt_sidor WHERE typ = 'kategori'")}
        for url in ordning:
            if url in sedda or len(ut) >= antal:
                continue
            sedda.add(url)
            r = c.execute("SELECT titel, typ, kategorier FROM sajt_sidor WHERE url = ?", (url,)).fetchone()
            if not r or r[1] not in ("post", "page"):
                continue
            kat = json.loads(r[2] or "[]")
            ut.append({"url": url, "titel": r[0], "etikett": kategorier.get(kat[0], "") if kat else ""})
    return ut


# ---------- Rendering ----------

def _e(v) -> str:
    return html.escape(str(v), quote=True)


class _Rendering:
    def __init__(self, titel: str, bilder: dict[int, str | None], redigerbar: bool):
        self.titel, self.bilder, self.redigerbar = titel, bilder, redigerbar

    def falt(self, sokvag: str, rik: bool = True) -> str:
        """data-attribut som gör ett textfält redigerbart i studions förhandsgranskning."""
        if not self.redigerbar:
            return ""
        return f' data-falt="{_e(sokvag)}" data-rik="{1 if rik else 0}"'

    def text(self, tagg: str, innehall: str, sokvag: str, rik: bool = True, klass: str = "", extra: str = "") -> str:
        k = f' class="{klass}"' if klass else ""
        varde = innehall if rik else _e(innehall)
        return f"<{tagg}{k}{extra}{self.falt(sokvag, rik)}>{varde}</{tagg}>"

    def eyebrow(self, text: str, sokvag: str) -> str:
        return self.text("span", text, sokvag, rik=False, klass="khd-eyebrow") if text else ""

    def bild(self, nummer: int | None) -> str | None:
        return self.bilder.get(nummer) if nummer else None

    # Block
    def block(self, b: dict, s: str) -> str:
        t = b["typ"]
        if t == "stycke":
            return self.text("p", b["text"], f"{s}.text")
        if t == "underrubrik":
            return self.text("h3", b["text"], f"{s}.text", rik=False)
        if t in ("lista", "checklista"):
            klass = ' class="khd-check"' if t == "checklista" else ""
            rader = "".join(self.text("li", p, f"{s}.punkter.{i}") for i, p in enumerate(b["punkter"]))
            return f"<ul{klass}>{rader}</ul>"
        if t == "steg":
            rader = "".join(
                f"<li>{self.text('strong', p['rubrik'], f'{s}.punkter.{i}.rubrik', rik=False) + ' ' if p['rubrik'] else ''}"
                f"{self.text('span', p['text'], f'{s}.punkter.{i}.text')}</li>" for i, p in enumerate(b["punkter"]))
            return f'<ol class="khd-steps">{rader}</ol>'
        if t == "notis":
            etikett = self.text("strong", b["etikett"], f"{s}.etikett", rik=False) + " " if b["etikett"] else ""
            return f'<div class="khd-note">{etikett}{self.text("span", b["text"], f"{s}.text")}</div>'
        if t == "kort":
            losning = b["variant"] == "losning"
            kort = []
            for i, p in enumerate(b["punkter"]):
                nummer = f"ÅTGÄRD {i + 1:02d}" if losning else f"{i + 1:02d}"
                kort.append(f'<div class="{"khd-solution" if losning else "khd-card"}"><span class="khd-number">{nummer}</span>'
                            f"{self.text('h3', p['rubrik'], f'{s}.punkter.{i}.rubrik', rik=False) if p['rubrik'] else ''}"
                            f"{self.text('p', p['text'], f'{s}.punkter.{i}.text')}</div>")
            return f'<div class="{"khd-solution-grid" if losning else "khd-card-grid"}">{"".join(kort)}</div>'
        if t == "tabell":
            huvud = "".join(f"<th>{_e(k)}</th>" for k in b["kolumner"])
            rader = "".join(
                "<tr>" + "".join(self.text("td", c, f"{s}.rader.{i}.{j}") for j, c in enumerate(rad)) + "</tr>"
                for i, rad in enumerate(b["rader"]))
            return (f'<div class="khd-table-scroll"><table class="khd-table"><thead><tr>{huvud}</tr></thead>'
                    f"<tbody>{rader}</tbody></table></div>")
        return ""

    def sektionstext(self, sek: dict, i: int) -> str:
        s = f"sektioner.{i}"
        delar = [self.eyebrow(sek["etikett"], f"{s}.etikett"),
                 self.text("h2", sek["rubrik"], f"{s}.rubrik", rik=False, extra=f' id="{_e(sek["id"])}"')]
        if sek["ingress"]:
            delar.append(self.text("p", sek["ingress"], f"{s}.ingress", klass="khd-lead"))
        delar += [self.block(b, f"{s}.block.{j}") for j, b in enumerate(sek["block"])]
        return "".join(delar)

    def sektion(self, sek: dict, i: int, omvand: bool) -> tuple[bool, str]:
        """(i_wrap, html). Mörka och lösningssektioner går i hela artikelns bredd."""
        text = self.sektionstext(sek, i)
        bild = self.bild(sek["bild"])
        if sek["layout"] == "mork":
            inre = "khd-feature-inner" + ("" if bild else " khd-feature-inner--utan-bild")
            media = f'<div class="khd-media">{bild}</div>' if bild else ""
            return False, f'<section class="khd-feature"><div class="{inre}"><div>{text}</div>{media}</div></section>'
        if sek["layout"] == "losningar":
            return False, f'<section class="khd-solutions"><div class="khd-wrap">{text}</div></section>'
        if sek["layout"] == "bild" and bild:
            klass = "khd-split khd-split--omvand" if omvand else "khd-split"
            return True, (f'<section class="khd-section"><div class="{klass}"><div>{text}</div>'
                          f'<div class="khd-media">{bild}</div></div></section>')
        return True, f'<section class="khd-section">{text}</section>'

    def rendera(self, dok: dict) -> str:
        hero, intro = dok["hero"], dok["intro"]
        ut = []
        huvudbild = self.bild(1)
        knapp = hero["knapp"]
        ut.append(
            f'<header class="khd-hero{"" if huvudbild else " khd-hero--utan-bild"}"><div class="khd-hero-copy">'
            f'{self.eyebrow(hero["etikett"], "hero.etikett")}{self.text("h1", self.titel, "titel", rik=False)}'
            f'{self.text("p", hero["ingress"], "hero.ingress", klass="khd-lead") if hero["ingress"] else ""}'
            + (f'<a class="khd-btn" href="#{_e(knapp["mal"])}">{_e(knapp["text"])} <span aria-hidden="true">→</span></a>' if knapp else "")
            + "</div>" + (f'<div class="khd-hero-photo">{huvudbild}</div>' if huvudbild else "") + "</header>")

        wrap = []
        if intro["rubrik"] or intro["stycken"]:
            fakta = intro["fakta"]
            text = (self.eyebrow(intro["etikett"], "intro.etikett")
                    + (self.text("h2", intro["rubrik"], "intro.rubrik", rik=False, extra=f' id="{_e(intro["id"])}"') if intro["rubrik"] else "")
                    + "".join(self.text("p", t, f"intro.stycken.{i}") for i, t in enumerate(intro["stycken"])))
            ruta = ""
            if fakta:
                ruta = ('<aside class="khd-facts">' + self.eyebrow(fakta["etikett"], "intro.fakta.etikett")
                        + (self.text("h3", fakta["rubrik"], "intro.fakta.rubrik", rik=False) if fakta["rubrik"] else "")
                        + "".join(f'<div class="khd-fact">{self.text("strong", p["titel"], f"intro.fakta.punkter.{i}.titel", rik=False)}'
                                  f'{self.text("p", p["text"], f"intro.fakta.punkter.{i}.text")}</div>'
                                  for i, p in enumerate(fakta["punkter"])) + "</aside>")
            wrap.append(f'<section class="khd-intro{"" if fakta else " khd-intro--utan-fakta"}"><div>{text}</div>{ruta}</section>')
        meny = [(s["id"], s["meny"] or s["rubrik"]) for s in dok["sektioner"]]
        if dok["faq"]:
            meny.append(("vanliga-fragor", "Vanliga frågor"))
        if len(meny) >= 3:
            wrap.append('<nav class="khd-toc" aria-label="Innehåll i guiden"><strong>I DEN HÄR GUIDEN</strong><div class="khd-toc-links">'
                        + "".join(f'<a href="#{_e(i)}">{_e(t)}</a>' for i, t in meny) + "</div></nav>")

        def stang_wrap():
            if wrap:
                ut.append('<div class="khd-wrap">' + "".join(wrap) + "</div>")
                wrap.clear()

        bildsektioner = 0
        for i, sek in enumerate(dok["sektioner"]):
            omvand = False
            if sek["layout"] == "bild" and self.bild(sek["bild"]):
                omvand = bildsektioner % 2 == 1
                bildsektioner += 1
            i_wrap, kod = self.sektion(sek, i, omvand)
            if i_wrap:
                wrap.append(kod)
            else:
                stang_wrap()
                ut.append(kod)

        sam, faq = dok["sammanfattning"], dok["faq"]
        if sam:
            wrap.append('<section class="khd-summary">' + self.eyebrow(sam["etikett"], "sammanfattning.etikett")
                        + self.text("h2", sam["rubrik"], "sammanfattning.rubrik", rik=False, extra=' id="sammanfattning"')
                        + "".join(self.text("p", t, f"sammanfattning.stycken.{i}") for i, t in enumerate(sam["stycken"]))
                        + "</section>")
        if faq:
            fragor = "".join(f'<details>{self.text("summary", f["fraga"], f"faq.fragor.{i}.fraga", rik=False)}'
                             f'{self.text("p", f["svar"], f"faq.fragor.{i}.svar")}</details>' for i, f in enumerate(faq["fragor"]))
            wrap.append('<section class="khd-section">' + self.eyebrow(faq["etikett"], "faq.etikett")
                        + self.text("h2", faq["rubrik"], "faq.rubrik", rik=False, extra=' id="vanliga-fragor"')
                        + f'<div class="khd-faq">{fragor}</div></section>')
        if dok["relaterade"]:
            kort = "".join(
                f'<a class="khd-related-card" href="{_e(r["url"])}">' + (f"<small>{_e(r['etikett'])}</small>" if r["etikett"] else "")
                + f"<h3>{_e(r['titel'])}</h3><span>Läs guiden →</span></a>" for r in dok["relaterade"])
            wrap.append('<section class="khd-related"><span class="khd-eyebrow">Fortsätt läsa</span>'
                        f'<h2 id="relaterade">{_e(dok["relaterade_rubrik"] or "Fler guider från Klimathemmet")}</h2>'
                        f'<div class="khd-related-grid">{kort}</div></section>')
        stang_wrap()
        return '<article class="khd-artikel">' + "".join(ut) + "</article>"


def rendera(innehall: dict, bilder: dict[int, str | None], redigerbar: bool = False) -> str:
    """Artikeln som HTML. bilder: bildnummer → färdig bild-HTML, eller None om bilden saknas.

    En sektion vars bild saknas renderas utan bildkolumn, så att layouten aldrig får tomma ytor.
    """
    dok = normalisera(innehall.get("dokument"))
    return _Rendering(innehall.get("titel", ""), bilder, redigerbar).rendera(dok)


def analys_html(innehall: dict) -> str:
    """HTML med bildmarkörer i bildplatserna; används av SEO-, länk- och kvalitetskontrollerna."""
    platser = bildplatser(innehall["dokument"])
    return rendera(innehall, {n: f"<!--BILD:{n}-->" for n in platser})


def bygg(innehall: dict, fordela_bilder: bool = False) -> dict:
    """Normaliserar dokumentet och uppdaterar den härledda HTML:en. Returnerar innehållet."""
    innehall["dokument"] = normalisera(innehall.get("dokument"), fordela_bilder=fordela_bilder)
    innehall["html"] = analys_html(innehall)
    return innehall


# ---------- Bilder ----------

def bild_wordpress(bild: dict, media: dict | None) -> str | None:
    """En bild från mediabiblioteket med srcset, width/height och lazy loading."""
    from .render import figure_html

    kod = figure_html(bild, media, klass="khd-bild", sizes="(max-width: 900px) 100vw, 640px")
    return kod or None


def bild_forhandsvisning(bild: dict | None, nummer: int, platshallare: bool = True) -> str | None:
    """Bilden som den visas i studion, eller en platshållare om den inte är skapad ännu."""
    if bild and bild.get("fil") and bild.get("url"):
        storlek = f' width="{int(bild["bredd"])}" height="{int(bild["hojd"])}"' if bild.get("bredd") and bild.get("hojd") else ""
        ladda = ' fetchpriority="high"' if nummer == 1 else ' loading="lazy"'
        return (f'<figure class="khd-bild" data-bild="{nummer}"><img src="{_e(bild["url"])}" alt="{_e(bild.get("alt", ""))}"'
                f'{storlek}{ladda} decoding="async"></figure>')
    if not platshallare:
        return None
    alt = _e((bild or {}).get("alt", ""))
    return (f'<div class="khd-platshallare" data-bild="{nummer}"><strong>Bild {nummer} saknas</strong>'
            f"<small>{alt}</small><small>Utan bild visas avsnittet i en spalt.</small></div>")
