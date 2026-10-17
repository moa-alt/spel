import time


def vanta_pa_status(klient, artikel_id, status, timeout=5):
    slut = time.time() + timeout
    while time.time() < slut:
        a = klient.get(f"/api/artiklar/{artikel_id}").json()
        if a["status"] == status:
            return a
        time.sleep(0.05)
    raise AssertionError(f"Fick {a['status']}, väntade {status}")


def skapa_och_generera(klient, sokord="ventilation i gamla hus"):
    a = klient.post("/api/artiklar", json={"huvudsokord": sokord, "sekundara_sokord": ["fukt"]}).json()
    assert a["status"] == "utkast"
    assert klient.post(f"/api/artiklar/{a['id']}/generera").status_code == 202
    return vanta_pa_status(klient, a["id"], "granskas")


def test_ko_ordning_och_import(klient):
    for s in ("a-sokord", "b-sokord"):
        klient.post("/api/artiklar", json={"huvudsokord": s})
    r = klient.post("/api/artiklar/import", content="c-sokord;x\na-sokord;dubblett\n".encode())
    assert r.json() == {"importerade": 1, "dubbletter": ["a-sokord"]}
    ids = [a["id"] for a in klient.get("/api/artiklar").json()["artiklar"]]
    klient.put("/api/artiklar/ordning", json={"ids": list(reversed(ids))})
    ordning = [a["huvudsokord"] for a in klient.get("/api/artiklar").json()["artiklar"]]
    assert ordning == ["c-sokord", "b-sokord", "a-sokord"]


def test_generera_alla_sparar_som_vantar_pa_granskning(klient):
    for s in ("fukt i källare", "tilläggsisolering vind"):
        klient.post("/api/artiklar", json={"huvudsokord": s})
    assert klient.post("/api/artiklar/generera-alla").json() == {"startade": 2}
    for a in klient.get("/api/artiklar").json()["artiklar"]:
        vanta_pa_status(klient, a["id"], "granskas")


def test_godkannande_kraver_granskning_och_upphavs_vid_redigering(klient):
    a = klient.post("/api/artiklar", json={"huvudsokord": "fönsterbyte"}).json()
    # Godkännande utan uttrycklig bekräftelse avvisas alltid.
    assert klient.post(f"/api/artiklar/{a['id']}/godkann", json={}).status_code == 400
    # Ett utkast utan innehåll kan inte godkännas.
    assert klient.post(f"/api/artiklar/{a['id']}/godkann", json={"bekraftad": True}).status_code == 409

    a = skapa_och_generera(klient)
    assert klient.post(f"/api/artiklar/{a['id']}/godkann", json={"bekraftad": True}).json() == {"status": "godkand"}
    # Dubbelklick ger inget andra godkännande.
    assert klient.post(f"/api/artiklar/{a['id']}/godkann", json={"bekraftad": True}).status_code == 409
    godkand = klient.get(f"/api/artiklar/{a['id']}").json()
    assert godkand["godkand_at"]

    # Att spara samma innehåll påverkar inte godkännandet.
    assert klient.put(f"/api/artiklar/{a['id']}/innehall", json=godkand["innehall"]).json() == {"status": "godkand"}

    andrad = dict(godkand["innehall"], titel="Ny titel")
    assert klient.put(f"/api/artiklar/{a['id']}/innehall", json=andrad).json() == {"status": "granskas"}
    efter = klient.get(f"/api/artiklar/{a['id']}").json()
    assert efter["godkand_at"] is None
    handlingar = [h["handling"] for h in efter["historik"]]
    assert handlingar[:2] == ["godkannande_upphavt", "godkand"]


def test_begar_andringar_och_generera_om(klient):
    a = skapa_och_generera(klient)
    assert klient.post(f"/api/artiklar/{a['id']}/begar-andringar", json={"kommentar": ""}).status_code == 400
    r = klient.post(f"/api/artiklar/{a['id']}/begar-andringar", json={"kommentar": "Kortare ingress"})
    assert r.json() == {"status": "utkast"}
    utkast = klient.get(f"/api/artiklar/{a['id']}").json()
    assert utkast["andringar"] == "Kortare ingress"
    # Manuellt åtgärdat utkast kan skickas till granskning igen ...
    assert klient.post(f"/api/artiklar/{a['id']}/skicka-till-granskning").json() == {"status": "granskas"}
    # ... eller genereras om; då nollställs ändringsbegäran.
    klient.post(f"/api/artiklar/{a['id']}/begar-andringar", json={"kommentar": "Fler exempel"})
    klient.post(f"/api/artiklar/{a['id']}/generera")
    assert vanta_pa_status(klient, a["id"], "granskas")["andringar"] is None


def test_avvisa_aterstall_aterkalla_och_ta_bort(klient):
    a = skapa_och_generera(klient)
    assert klient.post(f"/api/artiklar/{a['id']}/avvisa", json={"kommentar": "Fel ämne"}).json() == {"status": "avvisad"}
    assert klient.post(f"/api/artiklar/{a['id']}/godkann", json={"bekraftad": True}).status_code == 409
    assert klient.post(f"/api/artiklar/{a['id']}/aterstall").json() == {"status": "utkast"}
    klient.post(f"/api/artiklar/{a['id']}/skicka-till-granskning")
    klient.post(f"/api/artiklar/{a['id']}/godkann", json={"bekraftad": True})
    assert klient.post(f"/api/artiklar/{a['id']}/aterkalla").json() == {"status": "granskas"}
    assert klient.delete(f"/api/artiklar/{a['id']}").status_code == 204
    assert klient.get(f"/api/artiklar/{a['id']}").status_code == 404


def test_artiklar_sparas_permanent(klient, tmp_path):
    from app import db

    klient.post("/api/artiklar", json={"huvudsokord": "radiatorer", "sekundara_sokord": ["termostat"]})
    db.init()  # samma sak som händer vid omstart
    assert [a["huvudsokord"] for a in db.lista()] == ["radiatorer"]
    assert (tmp_path / "test.db").exists()


def test_omstart_under_generering_ger_status_fel(klient):
    from app import db

    artikel_id = db.skapa("golvvärme", [])
    db.byt_status(artikel_id, ("utkast",), "genereras")
    db.init()
    a = db.hamta(artikel_id)
    assert a["status"] == "fel" and "startades om" in a["fel"]
    assert klient.post(f"/api/artiklar/{artikel_id}/generera").status_code == 202
