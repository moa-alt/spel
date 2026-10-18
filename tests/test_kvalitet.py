"""Kvalitetskontrollen före godkännande, schemaläggning och publicering. Inga riktiga anrop görs."""

import asyncio
import re

import pytest

from conftest import ny_installning
from falsk_sajt import FalskSajt, transport
from test_bilder import FalskTjanst, bestall, bilder_klara, tjanst  # noqa: F401 (fixture)
from test_publicering import FalskWordPress, imorgon, kl, kor

BAS = "https://klimathemmet.se"


def ny_artikel(klient, sokord="ventilation i gamla hus"):
    from test_bilder import artikel_med_text

    return artikel_med_text(klient, sokord, sek=())


def andra(klient, aid, **falt):
    innehall = klient.get(f"/api/artiklar/{aid}").json()["innehall"] | falt
    assert klient.put(f"/api/artiklar/{aid}/innehall", json=innehall).status_code == 200


def kontroll(klient, aid):
    return klient.get(f"/api/artiklar/{aid}/kvalitet").json()


def koder(lista):
    return {p["kod"] for p in lista}


@pytest.fixture()
def sajt(klient, monkeypatch):
    from app import sajtkarta

    ny_installning(monkeypatch, WP_URL=BAS)
    s = FalskSajt(BAS)
    asyncio.run(sajtkarta.kartlagg(transport=transport(BAS, s)))
    return s


def test_demoartikeln_kan_godkannas_men_bara_med_faktagranskning(klient):
    aid = ny_artikel(klient)
    k = kontroll(klient, aid)
    assert k["kan_godkannas"] and not k["kritiska"]
    assert {"bild_saknas", "sokintention_saknas", "ingen_huvudbild"} <= koder(k["varningar"])
    assert any("kr" in f for f in k["fakta"]) and any("ROT" in f for f in k["fakta"])
    r = klient.post(f"/api/artiklar/{aid}/godkann", json={"bekraftad": True})
    assert r.status_code == 400 and "faktapåståenden" in r.json()["detail"]
    # Ett bra resultat godkänner aldrig artikeln automatiskt.
    assert klient.get(f"/api/artiklar/{aid}").json()["status"] == "granskas"
    assert klient.post(f"/api/artiklar/{aid}/godkann", json={"bekraftad": True, "fakta_verifierade": True}).status_code == 200


@pytest.mark.parametrize("falt,kod", [
    ({"titel": "En rubrik utan sökordet"}, "sokord_titel"),
    ({"meta_titel": ""}, "metatitel_saknas"),
    ({"meta_titel": "Något helt annat för villaägare i Sverige"}, "sokord_metatitel"),
    ({"meta_beskrivning": ""}, "metabeskrivning_saknas"),
    ({"slug": "Ogiltig Slug!"}, "slug_ogiltig"),
])
def test_sokord_och_metadata_blockerar(klient, falt, kod):
    aid = ny_artikel(klient)
    andra(klient, aid, **falt)
    k = kontroll(klient, aid)
    assert kod in koder(k["kritiska"]) and not k["kan_godkannas"]
    r = klient.post(f"/api/artiklar/{aid}/godkann", json={"bekraftad": True, "fakta_verifierade": True})
    assert r.status_code == 409 and "kan inte godkännas" in r.json()["detail"]


def test_rubrikstruktur(klient):
    aid = ny_artikel(klient)
    html_ = klient.get(f"/api/artiklar/{aid}").json()["innehall"]["html"]
    andra(klient, aid, html="<h1>Extra</h1>" + html_)
    assert "h1_i_text" in koder(kontroll(klient, aid)["kritiska"])
    andra(klient, aid, html="<p class=\"kh-ingress\">Ventilation i gamla hus.</p><h3>Fel nivå</h3><h2 id=\"a\">Ett</h2><p>Text</p>")
    k = kontroll(klient, aid)
    assert "for_fa_h2" in koder(k["kritiska"]) and {"h3_fore_h2", "kort_text"} <= koder(k["varningar"])


def test_bildfiler_alt_texter_och_testbilder(klient, tjanst, monkeypatch):  # noqa: F811
    from app import bilder, db

    aid = ny_artikel(klient)
    bestall(klient, aid, [1, 2])
    b = bilder_klara(aid)
    k = kontroll(klient, aid)
    assert not k["kritiska"] and "bild_saknas" in koder(k["varningar"]) and "ingen_huvudbild" not in koder(k["varningar"])
    (bilder.mapp(aid) / b[2]["fil"]).unlink()
    andra(klient, aid, bilder=[x | ({"alt": ""} if x["id"] == 1 else {}) for x in db.hamta(aid)["innehall"]["bilder"]])
    assert {"bildfil_saknas", "alt_saknas"} <= koder(kontroll(klient, aid)["kritiska"])

    def testbild(inn):
        inn["bilder"][0]["kalla"] = "test"
        return inn
    db.andra_innehall(aid, testbild, db.STATUSAR)
    assert "testbild" in koder(kontroll(klient, aid)["kritiska"])


def lank(vag, text="läs mer"):
    return f'<p>Här finns en länk: <a href="{BAS}{vag}">{text}</a>.</p>'


def med_lankar(klient, aid, *vagar):
    html_ = klient.get(f"/api/artiklar/{aid}").json()["innehall"]["html"]
    andra(klient, aid, html=html_ + "".join(lank(v, f"länk {i}") for i, v in enumerate(vagar)))


def test_interna_lankar_klassas(klient, sajt):
    aid = ny_artikel(klient, "isolera vinden")
    annan = ny_artikel(klient, "tilläggsisolering vind")
    annan_slug = klient.get(f"/api/artiklar/{annan}").json()["innehall"]["slug"]
    med_lankar(klient, aid, "/ftx-ventilation/", "/finns-inte/", f"/{annan_slug}/")
    klasser = {l["sokvag"]: l["klass"] for l in klient.get(f"/api/artiklar/{aid}").json()["lankklasser"]}
    assert klasser == {"/ftx-ventilation/": "publicerad", "/finns-inte/": "trasig", f"/{annan_slug}/": "ej_godkand"}
    k = kontroll(klient, aid)
    assert {"trasig_lank", "lank_ej_godkand"} <= koder(k["kritiska"])
    # Den andra artikeln godkänns och /finns-inte/ markeras uttryckligen som planerad: båda blir godkända framtida mål.
    klient.post(f"/api/artiklar/{annan}/godkann", json={"bekraftad": True, "fakta_verifierade": True})
    from app import db

    with db._conn() as c:
        c.execute("INSERT INTO planerade_mal (sokvag, artikel_id, status, bekraftad, uppdaterad) VALUES ('/finns-inte/', NULL, 'planerad', ?, ?)",
                  (db.nu(), db.nu()))
    k = kontroll(klient, aid)
    assert not k["kritiska"] and "lank_planerad" in koder(k["varningar"])


def test_utan_kartlaggning_kan_lankar_inte_verifieras(klient, monkeypatch):
    ny_installning(monkeypatch, WP_URL=BAS)
    aid = ny_artikel(klient)
    med_lankar(klient, aid, "/nagot/")
    assert "lank_okand" in koder(kontroll(klient, aid)["varningar"])


def test_lank_till_ej_publicerat_mal_blir_vanlig_text_vid_publicering(klient, sajt, monkeypatch):
    from app import db

    ny_installning(monkeypatch, WP_USER="moa", WP_APP_PASSWORD="abcd efgh ijkl", STUDIO_PUBLICERING="skarp")
    mal = ny_artikel(klient, "tilläggsisolering vind")
    klient.post(f"/api/artiklar/{mal}/godkann", json={"bekraftad": True, "fakta_verifierade": True})
    mal_slug = db.hamta(mal)["innehall"]["slug"]
    aid = ny_artikel(klient, "isolera vinden")
    med_lankar(klient, aid, "/ftx-ventilation/", f"/{mal_slug}/")
    klient.post(f"/api/artiklar/{aid}/godkann", json={"bekraftad": True, "fakta_verifierade": True})
    dag = imorgon()
    assert klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat(), "artikel_ids": [aid]}).status_code == 200
    wp = FalskWordPress()
    kor(kl(dag), wp)
    assert db.hamta(aid)["status"] == "publicerad"
    innehall = next(iter(wp.inlagg.values()))["content"]
    assert f'href="{BAS}/ftx-ventilation/"' in innehall
    assert f"/{mal_slug}/" not in innehall and "länk 1" in innehall
    # Det godkända innehållet är oförändrat; bara det som skickades till WordPress saknar länken.
    assert f"/{mal_slug}/" in db.hamta(aid)["innehall"]["html"]


def test_trasig_lank_efter_godkannandet_stoppar_publiceringen(klient, sajt, monkeypatch):
    from app import db

    ny_installning(monkeypatch, WP_USER="moa", WP_APP_PASSWORD="abcd efgh ijkl", STUDIO_PUBLICERING="skarp")
    aid = ny_artikel(klient, "isolera vinden")
    med_lankar(klient, aid, "/ftx-ventilation/")
    klient.post(f"/api/artiklar/{aid}/godkann", json={"bekraftad": True, "fakta_verifierade": True})
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    # Sidan försvinner från sajten innan publiceringsdagen.
    with db._conn() as c:
        c.execute("DELETE FROM sajt_sidor WHERE sokvag = '/ftx-ventilation/'")
    wp = FalskWordPress()
    kor(kl(dag), wp)
    assert wp.skapade == 0 and db.hamta(aid)["status"] == "godkand"
    logg = klient.get("/api/publicering").json()["logg"][0]
    assert logg["handling"] == "publicering_stoppad" and "/ftx-ventilation/" in logg["kommentar"]


def test_kannibalisering(klient, sajt):
    from app import db

    with db._conn() as c:
        c.execute("UPDATE sajt_sidor SET fokus = 'isolera vinden' WHERE sokvag = '/ftx-ventilation/'")
    aid = ny_artikel(klient, "isolera vinden")
    assert "kannibal_fokus" in koder(kontroll(klient, aid)["kritiska"])
    with db._conn() as c:
        c.execute("UPDATE sajt_sidor SET fokus = NULL")
    assert not kontroll(klient, aid)["kritiska"]
    # Ett snarlikt fokusnyckelord ger en varning.
    with db._conn() as c:
        c.execute("UPDATE sajt_sidor SET fokus = 'isolera vind' WHERE sokvag = '/ftx-ventilation/'")
    assert "kannibal_lik" in koder(kontroll(klient, aid)["varningar"])
    # En artikel med samma adress som en befintlig sida krockar i WordPress.
    vent = ny_artikel(klient, "ventilation i gamla hus")
    assert "slug_upptagen" in koder(kontroll(klient, vent)["kritiska"])
    # Samma huvudsökord två gånger i kön är kritiskt.
    klient.post("/api/artiklar", json={"huvudsokord": "Isolera  vinden"})
    assert "kannibal_ko" in koder(kontroll(klient, aid)["kritiska"])


def test_faktapastaenden_flaggas():
    from app.kvalitet import fakta

    html_ = ("<p>En luft-vattenvärmepump kostar ofta 100 000–150 000 kr installerad. Du sparar upp till 60 procent.</p>"
             "<p>Enligt Boverket gäller särskilda krav.</p><p>Det här är en vanlig mening utan påståenden.</p>"
             "<h2>Kostnad 2026 i kr</h2>")
    f = fakta({"html": html_})
    assert len(f) == 3 and any("150 000 kr" in x for x in f) and any("Boverket" in x for x in f)
    assert not any("vanlig mening" in x for x in f) and not any("Kostnad 2026" in x for x in f)


def test_kategori_som_inte_finns_ar_kritisk(klient, sajt):
    aid = ny_artikel(klient, "isolera vinden")
    assert "ingen_kategori" in koder(kontroll(klient, aid)["varningar"])
    assert klient.get("/api/wordpress/kategorier").json()["kategorier"] == [{"id": 11, "namn": "Fukt"}, {"id": 10, "namn": "Ventilation"}]
    andra(klient, aid, kategori_id=10)
    k = kontroll(klient, aid)
    assert "ingen_kategori" not in koder(k["varningar"]) and not k["kritiska"]
    andra(klient, aid, kategori_id=999)
    assert "okand_kategori" in koder(kontroll(klient, aid)["kritiska"])


def test_html_med_regex_specialtecken_i_lankar_ar_ofarligt(klient, sajt):
    aid = ny_artikel(klient, "isolera vinden")
    med_lankar(klient, aid, "/ftx-ventilation/?a=1&b=(2)")
    assert re.search("ftx", str(klient.get(f"/api/artiklar/{aid}").json()["lankklasser"]))
