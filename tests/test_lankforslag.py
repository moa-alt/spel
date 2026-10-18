import asyncio
import time

import httpx
import pytest

from conftest import ny_installning
from falsk_sajt import FalskSajt, transport

BAS = "https://klimathemmet.se"


@pytest.fixture()
def sajt(klient, monkeypatch):
    """Kartlagd sajt med WordPress-uppgifter, i simuleringsläge (standard)."""
    from app import sajtkarta

    ny_installning(monkeypatch, WP_URL=BAS, WP_USER="moa", WP_APP_PASSWORD="abcd efgh")
    s = FalskSajt(BAS)
    asyncio.run(sajtkarta.kartlagg(transport=transport(BAS, s)))
    return s


def sida_id(klient, titel):
    return next(n["id"] for n in klient.get("/api/seokarta").json()["noder"] if n["titel"] == titel)


def forslag(klient):
    return klient.get("/api/lankforslag").json()["forslag"]


def verkstall(ids, s):
    from app import lankforslag

    return asyncio.run(lankforslag.verkstall(ids, transport=transport(BAS, s)))


def test_beraknade_forslag_placeras_i_stycken_som_namner_amnet(klient, sajt):
    assert klient.post("/api/lankforslag/berakna").json()["nya"] >= 2
    f = {(x["kalla_titel"], x["mal_url"]): x for x in forslag(klient)}
    tips = f[("Ventilation i gamla hus – tips", f"{BAS}/ftx-ventilation/")]
    assert tips["ankartext"] == "FTX-ventilation" and tips["ankartext"] in tips["stycke"]
    assert tips["ursprung"] == "beraknat" and tips["status"] == "ny"
    assert ("FTX-ventilation i villa", f"{BAS}/fukt-i-kallare/") in f
    # Pelarsidan länkar redan till FTX-sidan: inget förslag på det.
    assert ("Ventilation i gamla hus", f"{BAS}/ftx-ventilation/") not in f
    # Ingen sida föreslås länka till sig själv, och inget förslag skapas två gånger.
    assert all(x["kalla_url"] != x["mal_url"] for x in f.values())
    antal = len(forslag(klient))
    klient.post("/api/lankforslag/berakna")
    assert len(forslag(klient)) == antal


def test_validering(klient, sajt):
    from app import db, lankforslag

    tips_id, pelare_id = sida_id(klient, "Ventilation i gamla hus – tips"), sida_id(klient, "FTX-ventilation i villa")
    with db._conn() as c:
        tips, ftx = lankforslag._sida(c, tips_id), lankforslag._sida(c, pelare_id)
    stycke = tips["stycken"][0]
    fukt = f"{BAS}/fukt-i-kallare/"
    assert "inte ordagrant" in lankforslag.validera(tips, fukt, "värmepump", stycke)
    assert "Stycket finns inte" in lankforslag.validera(tips, fukt, "FTX", "Påhittat stycke med FTX")
    assert "sig själv" in lankforslag.validera(tips, tips["url"], "FTX-ventilation", stycke)
    assert "för lång" in lankforslag.validera(tips, fukt, stycke, stycke)
    # FTX-sidan länkar redan till pelarsidan.
    assert "redan" in lankforslag.validera(ftx, f"{BAS}/ventilation-i-gamla-hus/", "fukt i källare", ftx["stycken"][0])
    assert lankforslag.validera(tips, fukt, "gammalt hus", stycke) is None
    # Allmänna ankartexter stoppas.
    with db._conn() as c:
        c.execute("UPDATE sajt_sidor SET stycken = ? WHERE id = ?", ('["Läs mer här om hur det fungerar i praktiken idag."]', tips_id))
        tips = lankforslag._sida(c, tips_id)
    assert "för allmän" in lankforslag.validera(tips, fukt, "Läs mer", tips["stycken"][0])


def test_godkannande_kravs_och_simulering_andrar_ingenting(klient, sajt):
    klient.post("/api/lankforslag/berakna")
    ids = [x["id"] for x in forslag(klient)]
    # Ej godkända förslag verkställs aldrig.
    assert verkstall(ids, sajt) == {"verkstallda": 0, "simulerade": 0, "konflikter": 0, "fel": 0}
    # API:t kräver uttrycklig bekräftelse.
    assert klient.post("/api/lankforslag/verkstall", json={"ids": ids}).status_code == 400
    assert klient.post("/api/lankforslag/godkann", json={"ids": ids}).json()["godkanda"] == len(ids)
    r = verkstall(ids, sajt)
    assert r["simulerade"] == len(ids) and r["verkstallda"] == 0
    assert sajt.skrivningar == []
    assert {x["status"] for x in forslag(klient)} == {"simulerad"}


def test_skarp_uppdatering_andrar_bara_ankartexten(klient, sajt, monkeypatch):
    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    klient.post("/api/lankforslag/berakna")
    tips = next(x for x in forslag(klient) if x["kalla_titel"] == "Ventilation i gamla hus – tips")
    fore = sajt.raw(4)
    klient.post("/api/lankforslag/godkann", json={"ids": [tips["id"]]})
    assert verkstall([tips["id"]], sajt)["verkstallda"] == 1
    efter = sajt.raw(4)
    lank = f'<a href="{BAS}/ftx-ventilation/">FTX-ventilation</a>'
    assert efter == fore.replace("FTX-ventilation", lank, 1)
    assert sajt.skrivningar == [("posts", 4)]
    status = {x["id"]: x["status"] for x in forslag(klient)}
    assert status[tips["id"]] == "verkstalld"
    # Kartan uppdateras direkt: tips-sidan länkar nu ut.
    k = klient.get("/api/seokarta").json()
    assert next(n for n in k["noder"] if n["titel"] == "Ventilation i gamla hus – tips")["ut"] == 1
    assert klient.get("/api/lankforslag").json()["andringar"][0]["status"] == "verkstalld"


def test_manuell_redigering_ger_konflikt_och_skrivs_inte_over(klient, sajt, monkeypatch):
    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    klient.post("/api/lankforslag/berakna")
    tips = next(x for x in forslag(klient) if x["kalla_titel"] == "Ventilation i gamla hus – tips")
    klient.post("/api/lankforslag/godkann", json={"ids": [tips["id"]]})
    manuell = sajt.raw(4) + "<p>Ny text som redaktören lade till.</p>"
    sajt.redigera_manuellt(4, manuell)
    assert verkstall([tips["id"]], sajt)["konflikter"] == 1
    assert sajt.raw(4) == manuell and sajt.skrivningar == []


def test_aterstallning(klient, sajt, monkeypatch):
    from app import lankforslag

    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    klient.post("/api/lankforslag/berakna")
    ids = [x["id"] for x in forslag(klient)]
    original = {pid: sajt.raw(pid) for pid in (2, 4)}
    klient.post("/api/lankforslag/godkann", json={"ids": ids})
    verkstall(ids, sajt)
    andringar = klient.get("/api/lankforslag").json()["andringar"]
    # Ingen redigering efteråt: exakt originalet tillbaka.
    forsta = next(a for a in andringar if a["titel"] == "FTX-ventilation i villa")
    asyncio.run(lankforslag.aterstall(forsta["id"], transport=transport(BAS, sajt)))
    assert sajt.raw(2) == original[2]
    # Redigering efteråt: bara studions länk tas bort, redaktörens text finns kvar.
    andra = next(a for a in andringar if a["titel"] == "Ventilation i gamla hus – tips")
    sajt.redigera_manuellt(4, sajt.raw(4) + "<p>Tillägg efteråt.</p>")
    asyncio.run(lankforslag.aterstall(andra["id"], transport=transport(BAS, sajt)))
    assert sajt.raw(4) == original[4] + "<p>Tillägg efteråt.</p>"
    with pytest.raises(lankforslag.LankFel):
        asyncio.run(lankforslag.aterstall(andra["id"], transport=transport(BAS, sajt)))


def test_redigera_ankartext_och_avvisa(klient, sajt):
    klient.post("/api/lankforslag/berakna")
    tips = next(x for x in forslag(klient) if x["kalla_titel"] == "Ventilation i gamla hus – tips")
    assert klient.patch(f"/api/lankforslag/{tips['id']}", json={"ankartext": "finns inte i texten"}).status_code == 409
    assert klient.patch(f"/api/lankforslag/{tips['id']}", json={"ankartext": "funderar på FTX-ventilation"}).json() == {
        "ankartext": "funderar på FTX-ventilation"}
    f = klient.get(f"/api/lankforslag/{tips['id']}/forhandsgranskning").json()
    assert f["fore_del"][1] == "funderar på FTX-ventilation" and "".join(f["fore_del"]) == f["fore"]
    klient.post("/api/lankforslag/avvisa", json={"ids": [tips["id"]]})
    assert tips["id"] not in [x["id"] for x in forslag(klient)]


def test_infoga_lank_respekterar_rubriker_och_befintliga_lankar(sajt):
    from app.lankforslag import LankFel, infoga_lank

    raw = '<h2>FTX-ventilation</h2><p>Om <a href="https://annan.se/">FTX-ventilation</a> och FTX-ventilation i hus.</p>'
    ut = infoga_lank(raw, "FTX-ventilation", "Om FTX-ventilation och FTX-ventilation i hus.", f"{BAS}/ftx/")
    assert ut == ('<h2>FTX-ventilation</h2><p>Om <a href="https://annan.se/">FTX-ventilation</a> och '
                  f'<a href="{BAS}/ftx/">FTX-ventilation</a> i hus.</p>')
    with pytest.raises(LankFel, match="redan"):
        infoga_lank(ut, "hus", "Om FTX-ventilation och FTX-ventilation i hus.", f"{BAS}/ftx/")
    with pytest.raises(LankFel, match="Stycket"):
        infoga_lank(raw, "FTX", "Ett annat stycke", f"{BAS}/x/")
    # HTML-entiteter i stycket hanteras.
    raw2 = "<p>Självdrag &amp; vädring fungerar ofta bra i gamla hus.</p>"
    assert "&amp; <a" in infoga_lank(raw2, "vädring", "Självdrag & vädring fungerar ofta bra i gamla hus.", f"{BAS}/v/")


def test_ny_artikel_far_inkommande_forslag_som_vantar_pa_publicering(klient, sajt):
    from app import db, lankforslag

    a = klient.post("/api/artiklar", json={"huvudsokord": "fukt i källare", "sekundara_sokord": ["FTX-ventilation"]}).json()
    klient.post(f"/api/artiklar/{a['id']}/generera")
    for _ in range(100):
        if db.hamta(a["id"])["status"] == "granskas":
            break
        time.sleep(0.02)
    inkommande = klient.get(f"/api/artiklar/{a['id']}").json()["inkommande_forslag"]
    assert inkommande and {x["status"] for x in inkommande} == {"vantar_pa_publicering"}
    # Kan inte godkännas förrän artikeln är publicerad.
    assert klient.post("/api/lankforslag/godkann", json={"ids": [inkommande[0]["id"]]}).json()["godkanda"] == 0
    lankforslag.artikel_publicerad(a["id"], f"{BAS}/fukt-i-kallare-guide/")
    efter = klient.get(f"/api/artiklar/{a['id']}").json()["inkommande_forslag"]
    assert {x["status"] for x in efter} == {"ny"} and {x["mal_url"] for x in efter} == {f"{BAS}/fukt-i-kallare-guide/"}


def test_sidans_detaljer_visar_nyckelord_och_rekommendationer(klient, sajt):
    klient.post("/api/lankforslag/berakna")
    d = klient.get(f"/api/seokarta/sida/{sida_id(klient, 'FTX-ventilation i villa')}").json()
    assert d["huvudnyckelord"] == "ftx-ventilation i villa" and d["nyckelord_kalla"] == "uppskattat från titeln"
    assert any(f["mal_url"] == f"{BAS}/ftx-ventilation/" for f in d["forslag"])
    k = klient.get("/api/seokarta").json()
    assert k["lankforslag_antal"]["ny"] >= 2
    assert "isolerade" in k["verifierat"] and "fa_inkommande" in k["verifierat"]


def test_skarp_utan_wordpress_uppgifter_ger_tydligt_fel(klient, sajt, monkeypatch):
    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp", WP_USER="", WP_APP_PASSWORD="")
    klient.post("/api/lankforslag/berakna")
    ids = [x["id"] for x in forslag(klient)]
    klient.post("/api/lankforslag/godkann", json={"ids": ids})
    r = klient.post("/api/lankforslag/verkstall", json={"ids": ids, "bekraftad": True})
    assert r.status_code == 409 and "WordPress-uppgifterna" in r.json()["detail"]


def test_ankartext_matchas_bara_som_hela_ord(sajt):
    from app.lankforslag import LankFel, _hitta_fras, infoga_lank

    stycke = "Många väljer FTX-ventilation när de renoverar sina gamla hus nuförtiden."
    assert _hitta_fras(stycke, "ventilation") is None
    assert _hitta_fras(stycke, "ftx-ventilation") == "FTX-ventilation"
    with pytest.raises(LankFel, match="sammanhängande"):
        infoga_lank(f"<p>{stycke}</p>", "ventilation", stycke, f"{BAS}/v/")
