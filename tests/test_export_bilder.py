"""Bilderna i WordPress-exporten (Kopiera kod och fliken WordPress-kod) och det godkända uppladdningssteget.

WordPress och bildtjänsten är mockade. Inga riktiga anrop görs och inget ändras på sajten.
"""

import asyncio
import re

import httpx
import pytest

from conftest import ny_installning
from test_bilder import WordPressMedMedia, artikel_med_text, bestall, bilder_klara, tjanst  # noqa: F401

WP = {"WP_URL": "https://klimathemmet.se", "WP_USER": "moa", "WP_APP_PASSWORD": "abcd efgh ijkl"}


@pytest.fixture()
def wp(klient, monkeypatch):
    """Studions WordPress-klient går mot ett mockat mediabibliotek."""
    from app import main
    from app.wordpress import WordPress

    ny_installning(monkeypatch, **WP)
    falsk = WordPressMedMedia()
    monkeypatch.setattr(main, "WordPress", lambda: WordPress(transport=httpx.MockTransport(falsk)))
    return falsk


def innehall(klient, aid):
    return klient.get(f"/api/artiklar/{aid}").json()["innehall"]


def exportera(klient, aid, inn=None):
    r = klient.post(f"/api/artiklar/{aid}/exportera", json=inn or innehall(klient, aid))
    assert r.status_code == 200, r.text
    return r.json()


def ladda_upp(klient, aid, bekraftad=True):
    return klient.post(f"/api/artiklar/{aid}/bilder/mediabiblioteket", json={"bekraftad": bekraftad})


def koder(klient, aid, del_="varningar"):
    return {p["kod"]: p["text"] for p in klient.get(f"/api/artiklar/{aid}").json()["kvalitet"][del_]}


def fyra_bilder(klient):
    aid = artikel_med_text(klient)
    bestall(klient, aid)
    bilder_klara(aid)
    return aid


# ---------- Före uppladdningen ----------

def test_utan_mediabiblioteket_listas_bilderna_som_saknas_i_koden(klient, tjanst, wp):
    aid = fyra_bilder(klient)
    res = exportera(klient, aid)
    assert res["saknas"] == [1, 2, 3, 4]
    assert [b["status"] for b in res["bilder"]] == ["ej_uppladdad"] * 4
    assert all("inte uppladdad till WordPress mediabibliotek" in b["text"] for b in res["bilder"])
    # Studions egna bildadresser kräver inloggning och får aldrig hamna i koden.
    assert "/api/bilder/" not in res["kod"] and "<img" not in res["kod"] and "<!--BILD" not in res["kod"]
    assert "1, 2, 3, 4" in koder(klient, aid)["bild_ej_i_mediabiblioteket"]
    # Ingenting har skickats till WordPress.
    assert wp.uppladdningar == 0 and not wp.inlagg


def test_uppladdningen_kraver_uttryckligt_godkannande(klient, tjanst, wp):
    aid = fyra_bilder(klient)
    r = ladda_upp(klient, aid, bekraftad=False)
    assert r.status_code == 400 and "Bekräfta" in r.json()["detail"]
    assert klient.post(f"/api/artiklar/{aid}/bilder/mediabiblioteket", json={}).status_code == 400
    assert wp.uppladdningar == 0


def test_uppladdning_utan_wordpress_ger_tydligt_fel(klient, tjanst):
    aid = fyra_bilder(klient)
    r = ladda_upp(klient, aid)
    assert r.status_code == 502 and "inte konfigurerat" in r.json()["detail"]


# ---------- Efter uppladdningen ----------

def test_alla_fyra_bilderna_foljer_med_i_ratt_sektioner_med_permanenta_adresser(klient, tjanst, wp):
    from app import config, db, design

    aid = fyra_bilder(klient)
    status_fore = db.hamta(aid)["status"]
    r = ladda_upp(klient, aid)
    assert r.status_code == 200, r.text
    assert r.json()["uppladdade"] == [1, 2, 3, 4]
    assert wp.uppladdningar == 4
    # Bara mediefiler: inget inlägg skapas, artikeln behåller sin status och publiceringsläget är orört.
    assert not wp.inlagg
    assert db.hamta(aid)["status"] == status_fore
    assert config.settings.publicering == "simulering"
    assert any(h["handling"] == "bilder_till_mediabiblioteket" for h in db.historik(aid))

    inn = innehall(klient, aid)
    res = exportera(klient, aid, inn)
    assert res["saknas"] == [] and all(b["status"] == "klar" for b in res["bilder"])
    kod = res["kod"]
    assert "/api/bilder/" not in kod
    bilder = {b["id"]: b for b in inn["bilder"]}
    adresser = {b["id"]: b["url"] for b in res["bilder"]}
    assert all(u.startswith("https://klimathemmet.se/wp-content/uploads/") for u in adresser.values())

    taggar = re.findall(r"<img [^>]+>", kod)
    assert len(taggar) == 4
    # Varje bild har sin alt-text, mått och srcset.
    for n, url in adresser.items():
        tagg = next(t for t in taggar if f'src="{url}"' in t)
        assert f'alt="{bilder[n]["alt"]}"' in tagg and bilder[n]["alt"]
        assert 'width="1536" height="1024"' in tagg and "srcset=" in tagg

    # Samma platser som i designen: huvudbilden i heron, bild 2–4 i sina sektioner, i läsordning.
    hero = re.search(r'<div class="khd-hero-photo">(.*?)</div>', kod, re.S).group(1)
    assert adresser[1] in hero
    ordning = design.bildplatser(inn["dokument"])
    assert sorted(ordning) == [1, 2, 3, 4]
    assert [n for n in sorted(adresser, key=lambda n: kod.index(adresser[n]))] == ordning
    for sek in inn["dokument"]["sektioner"]:
        if sek["bild"]:
            rubrik = kod.index(f'id="{sek["id"]}"')
            slut = kod.index("</section>", rubrik)
            assert adresser[sek["bild"]] in kod[rubrik:slut], f"bild {sek['bild']} ska ligga i sektionen {sek['id']}"

    # Koden är densamma som förhandsgranskningen, bortsett från bildadresserna.
    forhand = klient.post(f"/api/artiklar/{aid}/forhandsgranska", json=inn).json()["html"]
    assert [int(x) for x in re.findall(r'data-bild="(\d)"', forhand)] == ordning

    varningar = koder(klient, aid)
    assert "bild_ej_i_mediabiblioteket" not in varningar and "bild_saknas" not in varningar
    assert "Alla färdiga bilder finns i WordPress mediabibliotek" in klient.get(f"/api/artiklar/{aid}").json()["kvalitet"]["ok"]


def test_uppladdningen_ar_idempotent_och_uppdaterar_alt_texten(klient, tjanst, wp):
    aid = fyra_bilder(klient)
    ladda_upp(klient, aid)
    ladda_upp(klient, aid)
    assert wp.uppladdningar == 4
    inn = innehall(klient, aid)
    inn["bilder"][1]["alt"] = "Ljus tilluftsventil ovanför ett fönster i ett äldre trähus"
    klient.put(f"/api/artiklar/{aid}/innehall", json=inn)
    # Den kopierade koden får den nya alt-texten direkt; mediabiblioteket uppdateras vid nästa uppladdning.
    assert 'alt="Ljus tilluftsventil ovanför ett fönster i ett äldre trähus"' in exportera(klient, aid)["kod"]
    ladda_upp(klient, aid)
    assert wp.uppladdningar == 4
    assert wp.media[501]["alt_text"] == "Ljus tilluftsventil ovanför ett fönster i ett äldre trähus"


def test_osparade_textandringar_och_nya_bilder_i_exporten(klient, tjanst, wp):
    aid = fyra_bilder(klient)
    ladda_upp(klient, aid)
    # Exporten använder texten i redigeraren, även osparad.
    inn = innehall(klient, aid)
    inn["dokument"]["intro"]["rubrik"] = "En helt ny introduktion"
    assert "En helt ny introduktion" in exportera(klient, aid, inn)["kod"]
    # En bild som skapas om är en ny fil och måste laddas upp innan den följer med.
    bestall(klient, aid, [2])
    bilder_klara(aid)
    res = exportera(klient, aid)
    assert res["saknas"] == [2] and len(re.findall(r"<img ", res["kod"])) == 3
    assert "Bild 2" in koder(klient, aid)["bild_ej_i_mediabiblioteket"]
    ladda_upp(klient, aid)
    assert wp.uppladdningar == 5 and exportera(klient, aid)["saknas"] == []


def test_utkast_i_wordpress_sparar_adresserna_for_kopierad_kod(klient, tjanst, monkeypatch):
    """Bilder som laddats upp när artikeln skickades till WordPress följer också med i den kopierade koden."""
    from app import db
    from app.wordpress import WordPress

    ny_installning(monkeypatch, **WP)
    falsk = WordPressMedMedia()
    aid = fyra_bilder(klient)

    async def skicka():
        async with WordPress(transport=httpx.MockTransport(falsk)) as k:
            return await k.spara_inlagg(db.hamta(aid), "draft")
    asyncio.run(skicka())
    res = exportera(klient, aid)
    assert res["saknas"] == [] and len(re.findall(r"<img ", res["kod"])) == 4


# ---------- Saknade bilder och testbilder ----------

def test_saknade_bilder_syns_tydligt(klient, tjanst, wp):
    aid = artikel_med_text(klient)
    bestall(klient, aid, [1])
    bilder_klara(aid)
    varningar = [p for p in klient.get(f"/api/artiklar/{aid}").json()["kvalitet"]["varningar"] if p["kod"] == "bild_saknas"]
    assert len(varningar) == 3
    assert all("saknas" in p["text"] and "WordPress-koden" in p["text"] for p in varningar)
    res = exportera(klient, aid)
    assert {b["id"]: b["status"] for b in res["bilder"]} == {1: "ej_uppladdad", 2: "saknas", 3: "saknas", 4: "saknas"}
    assert "Bild 2 är inte skapad." in [b["text"] for b in res["bilder"]]


def test_testbilder_laddas_aldrig_upp(klient, monkeypatch):
    from app import main
    from app.wordpress import WordPress

    ny_installning(monkeypatch, STUDIO_BILDTJANST="test", **WP)
    falsk = WordPressMedMedia()
    monkeypatch.setattr(main, "WordPress", lambda: WordPress(transport=httpx.MockTransport(falsk)))
    aid = artikel_med_text(klient)
    bestall(klient, aid, [1])
    bilder_klara(aid)
    r = ladda_upp(klient, aid)
    assert r.status_code == 502 and "testbilder" in r.json()["detail"]
    assert falsk.uppladdningar == 0
    assert exportera(klient, aid)["bilder"][0]["status"] == "testbild"


def test_gransnittet_visar_bildstatus_och_uppladdningsknappen():
    from pathlib import Path

    rot = Path(__file__).resolve().parents[1] / "app" / "static"
    html, js = (rot / "index.html").read_text(), (rot / "app.js").read_text()
    assert 'id="codeBilder"' in html and 'id="mediaBtn"' in html
    # Kopiera kod och fliken WordPress-kod använder artikelns export med mediabibliotekets adresser.
    assert "/exportera`" in js and "/bilder/mediabiblioteket`" in js
    assert "confirm(" in js[js.index('$("#mediaBtn").addEventListener'):][:400]
