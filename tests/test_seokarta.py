import asyncio
import time

import httpx
import pytest

from conftest import ny_installning
from falsk_sajt import hanterare

BAS = "https://klimathemmet.se"


def falsk_sajt(request: httpx.Request) -> httpx.Response:
    return hanterare(BAS)(request)


@pytest.fixture()
def kartlagd(klient, monkeypatch):
    from app import sajtkarta

    ny_installning(monkeypatch, WP_URL=BAS)
    asyncio.run(sajtkarta.kartlagg(transport=httpx.MockTransport(falsk_sajt)))
    return klient


def nod(karta, titel):
    return next(n for n in karta["noder"] if n["titel"] == titel)


def test_kartlaggning_lankar_och_verifierade_problem(kartlagd):
    k = kartlagd.get("/api/seokarta").json()
    assert len(k["noder"]) == 8  # 5 artiklar, 1 sida, 2 kategorier
    pelaren = nod(k, "Ventilation i gamla hus")
    assert (pelaren["in"], pelaren["ut"], pelaren["trasiga"]) == (3, 3, 1)
    trasiga = k["verifierat"]["trasiga_lankar"]
    assert trasiga[0]["lankar"] == [{"url": "/borttagen-sida/", "ankartext": "en gammal guide"}]
    utan = {x["titel"] for x in k["verifierat"]["utan_inkommande"]}
    assert utan == {"Ventilation i gamla hus – tips", "Från självdrag till FTX-ventilation", "Offert på ventilation"}
    assert [x["titel"] for x in k["verifierat"]["saknar_metabeskrivning"]] == ["Fukt i källare"]


def test_kluster_pelarsida_och_sidor_efter_likhet(kartlagd):
    k = kartlagd.get("/api/seokarta").json()
    kluster = {c["namn"]: c for c in k["kluster"]}
    assert kluster["Ventilation"]["antal"] == 6  # 4 artiklar, kategorisidan och offertsidan (beräknat)
    assert nod(k, "Ventilation i gamla hus")["roll"] == "pelare"
    assert nod(k, "FTX-ventilation i villa")["roll"] == "stod"
    assert nod(k, "Ventilation")["roll"] == "kategori"
    assert nod(k, "Offert på ventilation")["kluster"] == kluster["Ventilation"]["id"]


def test_berakningar_kannibalisering_och_saknade_lankar(kartlagd):
    k = kartlagd.get("/api/seokarta").json()
    par = {(x["a_titel"], x["b_titel"]) for x in k["beraknat"]["mojlig_kannibalisering"]}
    assert ("Ventilation i gamla hus", "Ventilation i gamla hus – tips") in par
    saknade = {(x["a_titel"], x["b_titel"]) for x in k["beraknat"]["saknade_lankar"]}
    assert ("FTX-ventilation i villa", "Från självdrag till FTX-ventilation") in saknade


def test_sidans_detaljer_och_kommersiell_markering(kartlagd):
    k = kartlagd.get("/api/seokarta").json()
    offert = nod(k, "Offert på ventilation")
    assert kartlagd.post(f"/api/seokarta/sida/{offert['id']}/kommersiell", json={"kommersiell": True}).status_code == 200
    k = kartlagd.get("/api/seokarta").json()
    assert nod(k, "Offert på ventilation")["kommersiell"]
    assert [x["titel"] for x in k["beraknat"]["svaga_kommersiella"]] == ["Offert på ventilation"]
    d = kartlagd.get(f"/api/seokarta/sida/{nod(k, 'Ventilation i gamla hus')['id']}").json()
    assert {x["titel"] for x in d["inkommande"]} == {"FTX-ventilation i villa", "Fukt i källare", "Från självdrag till FTX-ventilation"}
    assert {x["status"] for x in d["utgaende"]} == {"ok", "trasig"}
    assert d["rubriker"] == ["Självdrag"]


def test_omkartlaggning_bevarar_kommersiella_markeringar(kartlagd):
    from app import sajtkarta

    offert = nod(kartlagd.get("/api/seokarta").json(), "Offert på ventilation")
    kartlagd.post(f"/api/seokarta/sida/{offert['id']}/kommersiell", json={"kommersiell": True})
    asyncio.run(sajtkarta.kartlagg(transport=httpx.MockTransport(falsk_sajt)))
    assert nod(kartlagd.get("/api/seokarta").json(), "Offert på ventilation")["kommersiell"]


def test_ai_forslag_valideras_och_kan_laggas_i_kon(kartlagd):
    from app import seo_ai

    sparade = seo_ai.spara_forslag([
        {"kluster": "Ventilation", "huvudsokord": "frånluftsventilation i radhus", "sekundara_sokord": ["F-system", "kostnad"],
         "sokintention": "informativ", "motivering": "Kompletterar FTX-artikeln.",
         "lanka_till": [f"{BAS}/ftx-ventilation/", f"{BAS}/pahittad-sida/"], "lanka_fran": [f"{BAS}/ventilation-i-gamla-hus/"]},
        {"kluster": "Ventilation", "huvudsokord": "ventilation i gamla hus", "sekundara_sokord": [],
         "sokintention": "informativ", "motivering": "x", "lanka_till": [], "lanka_fran": []},
    ])
    assert sparade[0]["lanka_till"] == [f"{BAS}/ftx-ventilation/"] and sparade[0]["borttagna_lankar"] == 1
    assert "Ventilation i gamla hus" in sparade[1]["krockar_med"]
    lista = kartlagd.get("/api/seokarta").json()["forslag"]
    assert len(lista) == 2
    artikel_id = kartlagd.post(f"/api/seokarta/forslag/{lista[0]['id']}/till-kon").json()["artikel_id"]
    a = kartlagd.get(f"/api/artiklar/{artikel_id}").json()
    assert a["huvudsokord"] == "frånluftsventilation i radhus" and a["sekundara_sokord"] == ["F-system", "kostnad"]
    assert a["lankplan"]["lanka_fran"] == [f"{BAS}/ventilation-i-gamla-hus/"]
    assert kartlagd.post(f"/api/seokarta/forslag/{lista[0]['id']}/till-kon").status_code == 409
    kartlagd.post(f"/api/seokarta/forslag/{lista[1]['id']}/avvisa")
    assert kartlagd.get("/api/seokarta").json()["forslag"] == []


def test_ai_forslag_kraver_nyckel(kartlagd):
    r = kartlagd.post("/api/seokarta/forslag")
    assert r.status_code == 409 and "ANTHROPIC_API_KEY" in r.json()["detail"]


def test_ny_artikel_lankar_bara_till_befintliga_sidor(kartlagd):
    from app import db, seo_ai

    r = kartlagd.post("/api/artiklar", json={"huvudsokord": "ventilation och fukt", "sekundara_sokord": ["FTX"]}).json()
    mal = seo_ai.lankmal_for(db.hamta(r["id"]))
    assert mal and all(m["url"].startswith(BAS) for m in mal)
    kartlagd.post(f"/api/artiklar/{r['id']}/generera")
    for _ in range(100):
        if db.hamta(r["id"])["status"] == "granskas":
            break
        time.sleep(0.02)
    a = kartlagd.get(f"/api/artiklar/{r['id']}").json()
    lankar = a["innehall"]["internlankar"]
    assert 1 <= len(lankar) <= 6
    assert all(l["finns"] for l in a["lankkontroll"])
    kanda = {n["url"] for n in kartlagd.get("/api/seokarta").json()["noder"]}
    assert {l["url"] for l in lankar} <= kanda


def test_lankkontroll_tar_bort_okanda_och_dubbla_lankar():
    from app.seo_ai import kontrollera_lankar

    html = (f'<p><a class="x" href="{BAS}/okand/">okänd</a> <a href="{BAS}/fukt/">fukt</a> '
            f'<a href="{BAS}/fukt/">igen</a> <a href="#toc">innehåll</a> <a href="https://annan.se/">extern</a></p>')
    ut, lankar = kontrollera_lankar(html, {f"{BAS}/fukt/"})
    assert ut == f'<p>okänd <a href="{BAS}/fukt/">fukt</a> igen <a href="#toc">innehåll</a> extern</p>'
    assert lankar == [{"url": f"{BAS}/fukt/", "ankartext": "fukt"}]


def test_kartlaggning_kraver_adress(klient):
    r = klient.post("/api/seokarta/kartlagg")
    assert r.status_code == 502 and "WP_URL" in r.json()["detail"]


def test_omkartlaggning_behaller_id(kartlagd):
    from app import sajtkarta

    fore = {n["url"]: n["id"] for n in kartlagd.get("/api/seokarta").json()["noder"]}
    asyncio.run(sajtkarta.kartlagg(transport=httpx.MockTransport(falsk_sajt)))
    efter = {n["url"]: n["id"] for n in kartlagd.get("/api/seokarta").json()["noder"]}
    assert fore == efter
