import asyncio
import time

import pytest

from conftest import ny_installning
from falsk_sajt import FalskSajt, transport

BAS = "https://klimathemmet.se"
KRYP = "/fukt-i-krypgrund/"


@pytest.fixture()
def sajt(klient, monkeypatch):
    from app import sajtkarta

    ny_installning(monkeypatch, WP_URL=BAS)
    s = FalskSajt(BAS)
    asyncio.run(sajtkarta.kartlagg(transport=transport(BAS, s)))
    return s


def saknade(klient):
    return {m["sokvag"]: m for m in klient.get("/api/saknade").json()["saknade"]}


def statistik(klient):
    return klient.get("/api/seokarta").json()["lankstatistik"]


def antal_artiklar(klient):
    return len(klient.get("/api/artiklar").json()["artiklar"])


def test_saknade_adresser_grupperas_med_sina_inkommande_lankar(klient, sajt):
    m = saknade(klient)
    assert set(m) == {KRYP, "/borttagen-sida/"}
    kryp = m[KRYP]
    assert kryp["antal"] == 2 and kryp["klass"] == "trasig" and kryp["url"] == BAS + KRYP
    assert {k["titel"] for k in kryp["kallor"]} == {"Fukt i källare", "Från självdrag till FTX-ventilation"}
    assert {k["ankartext"] for k in kryp["kallor"]} == {"fukt i krypgrund", "fukt i krypgrunden"}
    assert statistik(klient) == {"fungerande": 7, "trasiga": 3, "planerade_lankar": 0, "planerade_mal": 0}


def test_forslag_och_prioritering(klient, sajt):
    kryp = saknade(klient)[KRYP]
    f = kryp["forslag"]
    assert f["huvudsokord"] == "fukt i krypgrund" and f["sokintention"] == "informativ"
    assert f["kluster"] in {"Fukt", "Ventilation"} and "fukt i krypgrunden" in f["sekundara_sokord"]
    assert f["ursprung"] == "beraknat" and "2 befintliga artiklar" in f["motivering"]
    p = kryp["prioritet"]
    assert p["poang"] > 0 and p["niva"] in {"hög", "medel", "låg"} and "2 artiklar länkar hit" in p["skal"]


def test_slugens_tecken_aterstalls_fran_ankartexter():
    from app.saknade import _aterstall_tecken

    assert _aterstall_tecken(["fukt", "i", "kallare"], ["Fukt i källare", "källaren"]) == "fukt i källare"
    assert _aterstall_tecken(["varmepump"], []) == "varmepump"


def test_skapa_artikel_kraver_bekraftelse_och_lagger_bara_ett_utkast_i_kon(klient, sajt):
    fore = antal_artiklar(klient)
    data = {"sokvag": KRYP, "huvudsokord": "fukt i krypgrund", "sekundara_sokord": ["krypgrundsavfuktare"]}
    assert klient.post("/api/saknade/skapa-artikel", json=data).status_code == 400
    assert antal_artiklar(klient) == fore
    r = klient.post("/api/saknade/skapa-artikel", json=data | {"bekraftad": True})
    assert r.status_code == 201
    a = klient.get(f"/api/artiklar/{r.json()['artikel_id']}").json()
    assert a["status"] == "utkast" and not a["har_innehall"] and a["mal_slug"] == "fukt-i-krypgrund"
    assert set(a["lankplan"]["lanka_fran"]) == {f"{BAS}/fukt-i-kallare/", f"{BAS}/fran-ventilation-till-ftx/"}
    # Nu planerad, inte trasig – och räknas separat.
    kryp = saknade(klient)[KRYP]
    assert kryp["klass"] == "planerad" and kryp["artikel"]["status"] == "utkast"
    assert statistik(klient) == {"fungerande": 7, "trasiga": 1, "planerade_lankar": 2, "planerade_mal": 1}
    assert klient.post("/api/saknade/skapa-artikel", json=data | {"bekraftad": True}).status_code == 409
    # Ingenting skickades till WordPress.
    assert sajt.skrivningar == []


def test_artikel_i_kon_med_samma_adress_klassas_som_planerad(klient, sajt):
    assert saknade(klient)["/borttagen-sida/"]["klass"] == "trasig"
    a = klient.post("/api/artiklar", json={"huvudsokord": "borttagen sida"}).json()
    assert saknade(klient)["/borttagen-sida/"]["klass"] == "planerad"
    klient.post(f"/api/artiklar/{a['id']}/avvisa", json={"kommentar": ""})
    assert saknade(klient)["/borttagen-sida/"]["klass"] == "trasig"


def test_bekrafta_och_ta_bort_planering(klient, sajt):
    klient.post("/api/saknade/planera", json={"sokvag": "/borttagen-sida/"})
    m = saknade(klient)["/borttagen-sida/"]
    assert m["klass"] == "planerad" and m["bekraftad"] and m["artikel"] is None
    klient.post("/api/saknade/ta-bort-planering", json={"sokvag": "/borttagen-sida/"})
    assert saknade(klient)["/borttagen-sida/"]["klass"] == "trasig"
    assert klient.post("/api/saknade/planera", json={"sokvag": "/finns-inte/"}).status_code == 404


def test_adressen_ar_last_vid_generering_och_redigering(klient, sajt):
    from app import db

    r = klient.post("/api/saknade/skapa-artikel", json={"sokvag": KRYP, "huvudsokord": "fuktproblem under huset",
                                                        "bekraftad": True}).json()
    klient.post(f"/api/artiklar/{r['artikel_id']}/generera")
    for _ in range(100):
        if db.hamta(r["artikel_id"])["status"] == "granskas":
            break
        time.sleep(0.02)
    a = klient.get(f"/api/artiklar/{r['artikel_id']}").json()
    assert a["innehall"]["slug"] == "fukt-i-krypgrund"
    klient.put(f"/api/artiklar/{r['artikel_id']}/innehall", json=a["innehall"] | {"slug": "annan-adress", "titel": "Ny"})
    assert db.hamta(r["artikel_id"])["innehall"]["slug"] == "fukt-i-krypgrund"


def test_publicering_pa_ratt_adress_gor_lankarna_fungerande(klient, sajt):
    from app import db, saknade as s_mod, sajtkarta

    r = klient.post("/api/saknade/skapa-artikel", json={"sokvag": KRYP, "huvudsokord": "fukt i krypgrund", "bekraftad": True})
    artikel_id = r.json()["artikel_id"]
    # Fel adress upptäcks och loggas.
    assert "inte på /fukt-i-krypgrund/" in s_mod.artikel_publicerad(artikel_id, f"{BAS}/fukt-i-krypgrund-2/")
    assert s_mod.artikel_publicerad(artikel_id, f"{BAS}/fukt-i-krypgrund/") is None
    # Artikeln finns nu på sajten; efter en ny kartläggning fungerar länkarna.
    sajt.publicera(30, "fukt-i-krypgrund", "Fukt i krypgrund", "<p>Så åtgärdar du fukt i krypgrunden i ett äldre hus.</p>", 11)
    asyncio.run(sajtkarta.kartlagg(transport=transport(BAS, sajt)))
    assert KRYP not in saknade(klient)
    assert statistik(klient) == {"fungerande": 9, "trasiga": 1, "planerade_lankar": 0, "planerade_mal": 0}
    with db._conn() as c:
        assert c.execute("SELECT status FROM planerade_mal WHERE sokvag = ?", (KRYP,)).fetchone()[0] == "publicerad"


def test_inget_skapas_automatiskt(klient, sajt):
    fore = antal_artiklar(klient)
    for path in ("/api/saknade", "/api/seokarta", "/api/lankforslag"):
        klient.get(path)
    klient.post("/api/lankforslag/berakna")
    asyncio.run(_kor_schemalaggare_en_gang())
    assert antal_artiklar(klient) == fore and sajt.skrivningar == []


async def _kor_schemalaggare_en_gang():
    from app import publicering

    await publicering.kor_en_gang()


def test_ai_forslag_kraver_nyckel(klient, sajt):
    r = klient.post("/api/saknade/ai-forslag", json={"sokvag": KRYP})
    assert r.status_code == 409 and "ANTHROPIC_API_KEY" in r.json()["detail"]
