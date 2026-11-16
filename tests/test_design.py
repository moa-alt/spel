"""Designsystemet: strukturerat innehåll, deterministisk rendering, säker text, bilder och kompatibilitet."""

import asyncio
import copy
import json
import re
import time
from pathlib import Path

import httpx
import pytest

from conftest import ny_installning

ROT = Path(__file__).resolve().parent.parent


def dokument(**extra) -> dict:
    """Ett dokument som använder alla layouter och block."""
    d = {
        "hero": {"etikett": "VÄRME & ENERGI / GUIDE", "ingress": "Allt om <strong>värmepump i villa</strong>.",
                 "knapp": {"text": "Se åtgärderna", "mal": "atgarder"}},
        "intro": {"etikett": "Kort om ämnet", "rubrik": "Så fungerar en värmepump", "stycken": ["Första stycket.", "Andra stycket."],
                  "fakta": {"etikett": "Snabböversikt", "rubrik": "Värmepump", "punkter": [{"titel": "Passar för", "text": "Villor."}]}},
        "sektioner": [
            {"id": "sa-fungerar", "layout": "mork", "bild": 2, "etikett": "Tekniken", "rubrik": "Hur värmepumpen hämtar värme",
             "meny": "Tekniken", "block": [{"typ": "stycke", "text": "Text."}, {"typ": "underrubrik", "text": "Underrubrik"}]},
            {"id": "problem", "layout": "standard", "etikett": "Utmaningar", "rubrik": "Vanliga problem", "meny": "Problem",
             "ingress": "Ingress.", "block": [
                 {"typ": "kort", "variant": "problem", "punkter": [{"rubrik": "Buller", "text": "Text."}, {"rubrik": "Is", "text": "Text."}]},
                 {"typ": "notis", "etikett": "Viktigt:", "text": "Notis."}]},
            {"id": "val", "layout": "bild", "bild": 3, "etikett": "Välj", "rubrik": "Välj rätt modell", "meny": "Välj",
             "block": [{"typ": "checklista", "punkter": ["Ett", "Två"]}, {"typ": "lista", "punkter": ["A", "B"]}]},
            {"id": "jamforelse", "layout": "standard", "etikett": "Jämför", "rubrik": "Jämför systemen", "meny": "Jämför",
             "block": [{"typ": "tabell", "kolumner": ["System", "Fördel", "Nackdel"], "rader": [["Luft", "Billig", "Kallt"], ["Berg", "Effektiv", "Dyr"]]}]},
            {"id": "steg", "layout": "bild", "bild": 4, "etikett": "Steg", "rubrik": "Så installeras den", "meny": "Steg",
             "block": [{"typ": "steg", "punkter": [{"rubrik": "Planera.", "text": "Text."}, {"rubrik": "Installera.", "text": "Text."}]}]},
            {"id": "atgarder", "layout": "losningar", "etikett": "Åtgärder", "rubrik": "Fyra åtgärder", "meny": "Åtgärder",
             "block": [{"typ": "kort", "variant": "losning", "punkter": [{"rubrik": "Service", "text": "Text."}]}]},
        ],
        "sammanfattning": {"rubrik": "Sammanfattning om värmepump", "stycken": ["Summering."]},
        "faq": {"rubrik": "Vanliga frågor", "fragor": [{"fraga": "Vad kostar den?", "svar": "Svar."}]},
        "relaterade": [{"url": "https://klimathemmet.se/bergvarme/", "titel": "Bergvärme", "etikett": "Värme"}],
    }
    d.update(extra)
    return d


def innehall(dok=None, titel="Värmepump i villa: guide") -> dict:
    return {"titel": titel, "dokument": dok if dok is not None else dokument(), "bilder": [{"id": i, "alt": f"Bild {i} alt"} for i in range(1, 5)]}


def rendera(dok=None, bilder=None, redigerbar=False, titel="Värmepump i villa: guide"):
    from app import design

    bilder = {n: f'<figure class="khd-bild"><img src="/b{n}.webp" alt=""></figure>' for n in range(1, 5)} if bilder is None else bilder
    return design.rendera(innehall(dok, titel), bilder, redigerbar=redigerbar)


# ---------- Rendering av komponenterna ----------

def test_alla_komponenter_renderas_med_isolerade_klasser():
    h = rendera()
    assert h.startswith('<article class="khd-artikel">') and h.endswith("</article>")
    for klass in ("khd-hero", "khd-hero-photo", "khd-lead", "khd-btn", "khd-intro", "khd-facts", "khd-toc", "khd-feature",
                  "khd-feature-inner", "khd-card-grid", "khd-note", "khd-split", "khd-split--omvand", "khd-check",
                  "khd-table-scroll", "khd-table", "khd-steps", "khd-solutions", "khd-solution-grid", "khd-summary",
                  "khd-faq", "khd-related-card"):
        assert f'class="{klass}' in h or f" {klass}" in h, klass
    # Alla klasser i artikeln hör till designsystemet.
    klasser = {k for attr in re.findall(r'class="([^"]+)"', h) for k in attr.split()}
    assert all(k.startswith("khd-") for k in klasser), klasser
    # En H1 (i heron), H2 med unika id:n, innehållsförteckning som pekar på dem och knappen till rätt avsnitt.
    assert h.count("<h1") == 1 and "<h1>Värmepump i villa: guide</h1>" in h
    ids = re.findall(r'<h2 id="([^"]+)"', h)
    assert len(ids) == len(set(ids)) >= 8
    toc = re.search(r'<nav class="khd-toc".*?</nav>', h).group(0)
    assert re.findall(r'href="#([^"]+)"', toc) == ["sa-fungerar", "problem", "val", "jamforelse", "steg", "atgarder", "vanliga-fragor"]
    assert 'href="#atgarder"' in h.split("</header>")[0]
    assert '<span class="khd-number">01</span>' in h and '<span class="khd-number">ÅTGÄRD 01</span>' in h
    assert "<th>System</th>" in h and "<details><summary>Vad kostar den?</summary>" in h


def test_bildsektionerna_vaxlar_sida_och_helbredd_ligger_utanfor_spalten():
    h = rendera()
    splits = re.findall(r'class="(khd-split[^"]*)"', h)
    assert splits == ["khd-split", "khd-split khd-split--omvand"]
    # Mörka sektioner och lösningar går i full bredd, utanför khd-wrap.
    for klass in ("khd-feature", "khd-solutions"):
        fore = h[: h.index(f'<section class="{klass}"')]
        assert fore.count('<div class="khd-wrap">') == fore.count("</section></div>") or fore.rstrip().endswith("</div>")


def test_layouten_varierar_med_innehallet():
    from app import design

    enkel = dokument(sektioner=[{"id": "a", "layout": "standard", "rubrik": "Ett", "block": [{"typ": "stycke", "text": "x"}]}],
                     faq=None, sammanfattning=None, relaterade=[])
    h = rendera(enkel)
    assert "khd-table" not in h and "khd-card-grid" not in h and "khd-feature" not in h and "khd-toc" not in h
    assert rendera() != h
    assert design.normalisera(enkel)["faq"] is None


def test_renderingen_ar_deterministisk_och_normaliseringen_idempotent():
    from app import design

    assert rendera() == rendera()
    en = design.normalisera(dokument())
    assert design.normalisera(copy.deepcopy(en)) == en
    assert json.dumps(en, sort_keys=True) == json.dumps(design.normalisera(json.loads(json.dumps(en))), sort_keys=True)


# ---------- Säker texthantering ----------

ELAKT = '<script>alert(1)</script><img src=x onerror="alert(2)"><a href="javascript:alert(3)">klick</a><iframe src="//x"></iframe>'


def test_text_i_alla_falt_ar_saker():
    from app import design

    d = dokument()
    d["hero"]["etikett"] = ELAKT
    d["hero"]["ingress"] = ELAKT + " <strong>ok</strong>"
    d["hero"]["knapp"] = {"text": ELAKT, "mal": 'atgarder" onclick="x'}
    d["intro"]["rubrik"] = ELAKT
    d["sektioner"][0]["block"].append({"typ": "stycke", "text": ELAKT})
    d["sektioner"][0]["id"] = '"><script>x</script>'
    d["sektioner"][3]["block"][0]["rader"][0][1] = ELAKT
    d["faq"]["fragor"][0] = {"fraga": ELAKT, "svar": ELAKT}
    d["relaterade"] = [{"url": "javascript:alert(1)", "titel": "x"}, {"url": "https://klimathemmet.se/a/", "titel": ELAKT, "etikett": ELAKT}]
    for redigerbar in (False, True):
        h = rendera(d, redigerbar=redigerbar, titel=ELAKT)
        # Inga körbara taggar eller attribut; escapad text (t.ex. &lt;script&gt;) är ofarlig.
        assert not re.search(r"<(script|iframe|img src=x)|<[^>]*\son\w+=|href=\"javascript:", h), h
        assert "alert(1)" not in h.split("</header>")[1]
        assert "<strong>ok</strong>" in h
    # Text i rubriker visas som text, inte som HTML.
    assert "&lt;b&gt;" in rendera(dokument(intro={**dokument()["intro"], "rubrik": "<b>Fet</b> rubrik &lt;b&gt;"}))
    assert design.normalisera(d)["relaterade"][0]["url"] == "https://klimathemmet.se/a/"


def test_ogiltiga_block_och_layouter_tas_bort_eller_ersatts():
    from app import design

    d = dokument()
    d["sektioner"].append({"id": "x", "layout": "okand", "rubrik": "Okänd", "bild": 2,
                           "block": [{"typ": "okand"}, {"typ": "tabell", "kolumner": ["En"], "rader": [["a"]]}, {"typ": "stycke", "text": ""},
                                     {"typ": "stycke", "text": "Kvar"}]})
    d["sektioner"].append({"rubrik": "", "block": [{"typ": "stycke", "text": "Ingen rubrik"}]})
    n = design.normalisera(d)
    sista = n["sektioner"][-1]
    assert sista["layout"] == "standard" and sista["bild"] is None and sista["block"] == [{"typ": "stycke", "text": "Kvar"}]
    assert len(n["sektioner"]) == len(dokument()["sektioner"]) + 1


# ---------- Bilder ----------

def test_saknade_bilder_ger_hel_layout_utan_tomma_ytor():
    h = rendera(bilder={})
    assert "khd-hero--utan-bild" in h and "khd-hero-photo" not in h
    assert "khd-feature-inner--utan-bild" in h and "khd-split" not in h and "<img" not in h and "khd-media" not in h
    # Bara en bild finns: huvudbilden; de andra sektionerna blir enspaltiga.
    h = rendera(bilder={1: '<figure class="khd-bild"><img src="/a.webp" alt=""></figure>'})
    assert "khd-hero-photo" in h and h.count("<img") == 1 and "khd-split" not in h


def test_bilderna_fordelas_nar_ai_inte_placerat_dem():
    from app import design

    d = dokument()
    for s in d["sektioner"]:
        s.pop("bild", None)
    d["sektioner"][1]["bild"] = 3  # standardlayout får ingen bild
    n = design.normalisera(d, fordela_bilder=True)
    placerade = {s["bild"]: s for s in n["sektioner"] if s["bild"]}
    assert set(placerade) == {2, 3, 4}
    assert all(s["layout"] in ("mork", "bild") for s in placerade.values())
    assert not any(b["typ"] in ("kort", "tabell") for s in placerade.values() for b in s["block"])
    assert design.bildplatser(n)[0] == 1
    # En bild används bara en gång.
    d = dokument()
    d["sektioner"][2]["bild"] = 2
    assert [s["bild"] for s in design.normalisera(d)["sektioner"]].count(2) == 1


def test_wordpress_bilder_far_srcset_och_lazy_loading():
    from app.render import wordpress_block

    inn = innehall()
    media = {n: {"id": 500 + n, "url": f"https://klimathemmet.se/wp-content/uploads/b{n}.webp", "bredd": 1536, "hojd": 1024,
                 "storlekar": [{"url": f"https://klimathemmet.se/wp-content/uploads/b{n}-768x512.webp", "bredd": 768}]} for n in range(1, 5)}
    kod = wordpress_block(inn, inline_css=False, media=media)
    imgs = re.findall(r"<img [^>]+>", kod)
    assert len(imgs) == 4 and all('class="wp-image-' in i and "srcset=" in i and 'width="1536"' in i for i in imgs)
    assert 'fetchpriority="high"' in imgs[0] and all('loading="lazy"' in i for i in imgs[1:])
    assert 'alt="Bild 1 alt"' in imgs[0]
    assert "<style>" not in kod and kod.startswith("<!-- wp:html -->") and "data-falt" not in kod
    # Med ett äldre plugin bäddas designens CSS in, scopad till artikeln.
    assert ".khd-artikel" in wordpress_block(inn, inline_css=True, media=media).split("</style>")[0]


# ---------- Studion: förhandsgranskning, redigering och export ----------

def _vanta_granskas(klient, aid):
    from app import db

    for _ in range(200):
        if db.hamta(aid)["status"] == "granskas":
            return
        time.sleep(0.02)
    raise AssertionError("genereringen blev aldrig klar")


@pytest.fixture()
def artikel(klient):
    a = klient.post("/api/artiklar", json={"huvudsokord": "värmepump i villa", "sekundara_sokord": ["luft-vattenvärmepump"]}).json()
    klient.post(f"/api/artiklar/{a['id']}/generera")
    _vanta_granskas(klient, a["id"])
    return a["id"]


def _utan_redigering(h: str) -> str:
    return re.sub(r' data-falt="[^"]*" data-rik="[01]"', "", h)


def test_forhandsgranskningen_ar_samma_design_som_exporten(klient, artikel):
    from app import design

    a = klient.get(f"/api/artiklar/{artikel}").json()
    r = klient.post(f"/api/artiklar/{artikel}/forhandsgranska", json=a["innehall"]).json()
    assert 'data-falt="hero.ingress"' in r["html"] and r["html"].count("khd-platshallare") == 4
    kod = klient.post("/api/exportera", json=a["innehall"]).json()["kod"]
    assert ".khd-artikel" in kod.split("</style>")[0] and "khd-platshallare" not in kod
    assert "data-falt" not in kod and "/api/bilder/" not in kod
    # Med samma bilder ger förhandsgranskningen och exporten exakt samma HTML, bortsett från redigeringsattributen.
    platser = {n: f"<figure>{n}</figure>" for n in range(1, 5)}
    assert _utan_redigering(design.rendera(a["innehall"], platser, redigerbar=True)) == design.rendera(a["innehall"], platser)


def test_redigering_sparas_strukturerat_och_sakert(klient, artikel):
    from app import db

    a = klient.get(f"/api/artiklar/{artikel}").json()
    inn = a["innehall"]
    inn["dokument"]["sektioner"][0]["block"][0]["text"] = 'Ny <strong>text</strong> <script>alert(1)</script><a href="javascript:x">l</a>'
    inn["dokument"]["intro"]["rubrik"] = "Ny <em>rubrik</em>"
    inn["html"] = "<p>Egen HTML från webbläsaren</p>"
    r = klient.put(f"/api/artiklar/{artikel}/innehall", json=inn).json()
    sparad = db.hamta(artikel)["innehall"]
    assert sparad["dokument"]["sektioner"][0]["block"][0]["text"] == "Ny <strong>text</strong> <a>l</a>"
    assert sparad["dokument"]["intro"]["rubrik"] == "Ny rubrik"
    # HTML:en härleds alltid från dokumentet; webbläsarens HTML används aldrig.
    assert "Egen HTML" not in sparad["html"] and sparad["html"] == r["html"] and "Ny <strong>text</strong>" in sparad["html"]
    assert "<script" not in sparad["html"]


def test_metadata_och_bilder_kan_fortfarande_redigeras(klient, artikel):
    from app import db

    inn = klient.get(f"/api/artiklar/{artikel}").json()["innehall"]
    inn["meta_titel"] = "Värmepump i villa – ny metatitel för test"
    inn["bilder"][1]["alt"] = "Utomhusdel till en värmepump på en husvägg i snö"
    klient.put(f"/api/artiklar/{artikel}/innehall", json=inn)
    sparad = db.hamta(artikel)["innehall"]
    assert sparad["meta_titel"].endswith("för test") and sparad["bilder"][1]["alt"].startswith("Utomhusdel")
    r = klient.post(f"/api/artiklar/{artikel}/forhandsgranska", json=sparad).json()
    assert "Utomhusdel till en värmepump" in r["html"]


def test_kvalitet_och_seo_fungerar_pa_strukturerade_artiklar(klient, artikel):
    a = klient.get(f"/api/artiklar/{artikel}").json()
    k = a["kvalitet"]
    assert k["kan_godkannas"] and "h1_antal" not in {p["kod"] for p in k["kritiska"]}
    assert a["innehall"]["html"].count("<!--BILD:") == 4
    inn = a["innehall"]
    inn["dokument"]["faq"] = None
    inn["dokument"]["sammanfattning"] = None
    klient.put(f"/api/artiklar/{artikel}/innehall", json=inn)
    varningar = {p["kod"] for p in klient.get(f"/api/artiklar/{artikel}/kvalitet").json()["varningar"]}
    assert {"faq_saknas", "sammanfattning_saknas"} <= varningar


def test_lankar_i_textfalten_kontrolleras_over_hela_artikeln():
    from app import design

    d = design.normalisera(dokument())
    kand = "https://klimathemmet.se/bergvarme/"
    d["intro"]["stycken"][0] = f'Se <a href="{kand}">bergvärme</a> och <a href="https://klimathemmet.se/finns-inte/">okänd</a>.'
    d["sektioner"][0]["block"][0]["text"] = f'Igen <a href="{kand}">bergvärme</a>.'
    lankar = design.rensa_lankar(d, {kand})
    assert lankar == [{"url": kand, "ankartext": "bergvärme"}]
    assert d["intro"]["stycken"][0] == f'Se <a href="{kand}">bergvärme</a> och okänd.'
    assert d["sektioner"][0]["block"][0]["text"] == "Igen bergvärme."


# ---------- AI-svaret ----------

def test_ai_svaret_blir_ett_strukturerat_dokument():
    from app import ai_writer
    from test_ai_writer import _artikeldata, ai_dokument

    # Claude har inte placerat några bilder (bild 0 överallt): servern fördelar dem.
    platt = ai_dokument()
    for s in platt["sektioner"]:
        s["bild"] = 0
    data = _artikeldata(platt) | {"slug": "värmepump i villa"}
    a = ai_writer._artikel_fran_json(data, "värmepump i villa", [])
    assert a["slug"] == "varmepump-i-villa" and a["dokument"]["version"] == 2
    assert [b["id"] for b in a["bilder"]] == [1, 2, 3, 4]
    assert sorted(n for n in (s["bild"] for s in a["dokument"]["sektioner"]) if n) == [2, 3, 4]
    assert a["html"].count("<!--BILD:") == 4 and "<h1>" in a["html"]


def test_ai_schemat_foljer_reglerna_for_strukturerade_svar():
    from app.ai_writer import ARTIKEL_SCHEMA

    def objekt(s):
        if isinstance(s, dict):
            if s.get("type") == "object":
                yield s
            for v in s.values():
                yield from objekt(v)
        elif isinstance(s, list):
            for v in s:
                yield from objekt(v)
    alla = list(objekt(ARTIKEL_SCHEMA))
    # Alla egenskaper är obligatoriska (valfria egenskaper gör grammatiken för stor).
    assert len(alla) > 10 and all(o.get("additionalProperties") is False and set(o["required"]) == set(o["properties"]) for o in alla)
    text = json.dumps(ARTIKEL_SCHEMA)
    assert not any(n in text for n in ("minItems", "maxItems", "minLength", "maxLength", "minimum", "maximum"))


# ---------- Kompatibilitet med äldre artiklar ----------

def test_aldre_artiklar_renderas_som_forut(klient):
    from app import db
    from app.render import wordpress_block

    a = klient.post("/api/artiklar", json={"huvudsokord": "fukt i källare"}).json()
    aldre = {"titel": "Fukt i källare", "slug": "fukt-i-kallare", "meta_titel": "Fukt i källare – guide", "meta_beskrivning": "Om fukt.",
             "html": '<p class="kh-ingress">Fukt i källare.</p><!--BILD:1--><h2 id="a">Rubrik</h2><div class="kh-tips"><p>Tips</p></div>',
             "bilder": [{"id": 1, "alt": "Källare", "bildtext": "", "prompt": "", "url": None}], "huvudsokord": "fukt i källare",
             "sekundara_sokord": []}
    db.byt_status(a["id"], ("utkast",), "granskas", innehall=aldre)
    kod = wordpress_block(db.hamta(a["id"])["innehall"])
    assert '<div class="kh-artikel">' in kod and "kh-tips" in kod and "khd-" not in kod
    assert ".kh-artikel" in kod.split("</style>")[0]
    # Förhandsgranskningen på servern gäller bara det nya formatet; äldre artiklar redigeras som HTML som förut.
    assert klient.post(f"/api/artiklar/{a['id']}/forhandsgranska", json=aldre).status_code == 409
    r = klient.put(f"/api/artiklar/{a['id']}/innehall", json=aldre | {"html": aldre["html"] + "<p>Mer text.</p>"})
    assert r.status_code == 200 and db.hamta(a["id"])["innehall"]["html"].endswith("<p>Mer text.</p>")
    assert not db.hamta(a["id"])["innehall"].get("dokument")
    # Webbläsaren kan inte göra om en äldre artikel till det nya formatet.
    klient.put(f"/api/artiklar/{a['id']}/innehall", json=aldre | {"dokument": dokument()})
    assert not db.hamta(a["id"])["innehall"].get("dokument")


def test_plugin_version_avgor_om_css_baddas_in(klient, artikel, monkeypatch):
    from app import db
    from app.wordpress import WordPress
    from test_publicering import FalskWordPress

    ny_installning(monkeypatch, WP_URL="https://klimathemmet.se", WP_USER="moa", WP_APP_PASSWORD="abcd efgh ijkl")
    for version, inbaddad in (("1.2.0", False), ("1.1.0", True)):
        wp = FalskWordPress(plugin_version=version)

        async def skicka():
            async with WordPress(transport=httpx.MockTransport(wp)) as k:
                return await k.spara_inlagg(db.hamta(artikel), "draft")
        asyncio.run(skicka())
        innehall = next(iter(wp.inlagg.values()))["content"]
        assert ('<article class="khd-artikel">' in innehall) and (("<style>" in innehall) is inbaddad)


def test_pluginet_innehaller_designens_stilmall(tmp_path):
    import sys
    import zipfile

    sys.path.insert(0, str(ROT / "scripts"))
    import bygg_wp_plugin

    zip_ = bygg_wp_plugin.bygg(tmp_path / "p.zip")
    with zipfile.ZipFile(zip_) as z:
        assert z.read("klimathemmet-studio/design.css") == (ROT / "app/static/design.css").read_bytes()
        php = next(z.read(n).decode() for n in z.namelist() if n.endswith(".php"))
    assert "Version: 1.2.0" in php and "'khd-artikel'" in php and "design.css" in php


# ---------- CSS ----------

def test_css_ar_isolerad_och_responsiv():
    css = (ROT / "app/static/design.css").read_text(encoding="utf-8")
    utan_kommentarer = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    selektorer = []
    for regel in re.findall(r"([^{}@]+)\{[^{}]*\}", utan_kommentarer):
        selektorer += [s.strip() for s in regel.split(",") if s.strip()]
    assert selektorer and all(s.startswith(".khd-artikel") for s in selektorer), [s for s in selektorer if not s.startswith(".khd-artikel")]
    assert "container-type: inline-size" in css
    assert "@container khd-artikel (max-width: 900px)" in css and "@container khd-artikel (max-width: 600px)" in css
    for farg in ("#19382b", "#f7f4ee", "#eaf0e8", "#315943"):
        assert farg in css
    assert "prefers-reduced-motion" in css
