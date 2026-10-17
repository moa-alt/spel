import asyncio
import json
import sqlite3
import time as time_mod
from datetime import date, datetime, time, timedelta, timezone

import httpx
import pytest

from conftest import ny_installning

STHLM_7 = time(7, 0)


class FalskWordPress:
    """Minimal WordPress REST API i minnet, med Yoast och tilläggspluginet installerade."""

    def __init__(self, plugin=True):
        self.inlagg: dict[int, dict] = {}
        self.plugin = plugin
        self.fel_kvar = 0
        self.skapade = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/wp-json")
        if self.fel_kvar:
            self.fel_kvar -= 1
            return httpx.Response(503)
        if path == "/":
            ns = ["wp/v2", "yoast/v1"] + (["klimathemmet-studio/v1"] if self.plugin else [])
            return httpx.Response(200, json={"namespaces": ns})
        if path == "/wp/v2/users/me":
            return httpx.Response(200, json={"name": "Moa", "capabilities": {"publish_posts": True}})
        if path == "/wp/v2/posts" and request.method == "OPTIONS":
            meta = {"_yoast_wpseo_title": {}, "_yoast_wpseo_metadesc": {}, "_kh_studio_id": {}} if self.plugin else {}
            return httpx.Response(200, json={"schema": {"properties": {"meta": {"properties": meta}}}})
        if path == "/wp/v2/posts" and request.method == "GET":
            slug = request.url.params.get("slug")
            return httpx.Response(200, json=[p for p in self.inlagg.values() if p["slug"] == slug])
        if path == "/wp/v2/posts" and request.method == "POST":
            self.skapade += 1
            pid = 100 + len(self.inlagg)
            self.inlagg[pid] = self._spara({"id": pid, "meta": {}}, json.loads(request.content))
            return httpx.Response(201, json=self.inlagg[pid])
        if path.startswith("/wp/v2/posts/"):
            pid = int(path.rsplit("/", 1)[1])
            if pid not in self.inlagg:
                return httpx.Response(404, json={"code": "rest_post_invalid_id"})
            if request.method == "POST":
                self._spara(self.inlagg[pid], json.loads(request.content))
            return httpx.Response(200, json=self.inlagg[pid])
        return httpx.Response(404)

    @staticmethod
    def _spara(post, data):
        post.update({k: v for k, v in data.items() if k != "meta"})
        post["meta"] = {**post.get("meta", {}), **data.get("meta", {})}
        post["link"] = f"https://klimathemmet.se/{post['slug']}/"
        return post


@pytest.fixture()
def wp(klient, monkeypatch):
    ny_installning(monkeypatch, WP_URL="https://klimathemmet.se", WP_USER="moa", WP_APP_PASSWORD="abcd efgh ijkl")
    return FalskWordPress()


def godkand_artikel(klient, sokord="ventilation i gamla hus"):
    from app import db, jobb

    a = klient.post("/api/artiklar", json={"huvudsokord": sokord}).json()
    klient.post(f"/api/artiklar/{a['id']}/generera")
    for _ in range(100):
        if db.hamta(a["id"])["status"] == "granskas":
            break
        time_mod.sleep(0.02)
    assert klient.post(f"/api/artiklar/{a['id']}/godkann", json={"bekraftad": True}).status_code == 200
    return a["id"]


def imorgon():
    from app import publicering

    return publicering.idag_lokalt() + timedelta(days=1)


def kor(nu, wp=None):
    from app import publicering

    asyncio.run(publicering.kor_en_gang(nu=nu, transport=httpx.MockTransport(wp) if wp else None))


def kl(dag, timme=7, minut=1):
    from app import publicering

    return publicering.lokal_tid(dag, time(timme, minut)).astimezone(timezone.utc)


def test_bara_godkanda_artiklar_kan_schemalaggas(klient):
    a = klient.post("/api/artiklar", json={"huvudsokord": "fukt"}).json()
    r = klient.post("/api/publicering/schemalagg", json={"startdatum": imorgon().isoformat(), "artikel_ids": [a["id"]]})
    assert r.status_code == 409 and "inte godkänd" in r.json()["detail"]


def test_en_artikel_per_dag_i_kons_ordning_och_upptagna_dagar_hoppas_over(klient):
    ids = [godkand_artikel(klient, s) for s in ("fukt i källare", "värmepump", "radonsanering")]
    klient.put("/api/artiklar/ordning", json={"ids": list(reversed(ids))})
    start = imorgon()
    # Första artikeln schemaläggs ensam på dag 2, så att dag 2 är upptagen.
    r = klient.post("/api/publicering/schemalagg", json={"startdatum": (start + timedelta(days=1)).isoformat(),
                                                          "artikel_ids": [ids[0]]})
    assert r.status_code == 200
    r = klient.post("/api/publicering/schemalagg", json={"startdatum": start.isoformat(), "klockslag": "08:30"}).json()
    datum = [s["datum"] for s in r["schemalagda"]]
    assert datum == [start.isoformat(), (start + timedelta(days=2)).isoformat()]
    assert [s["artikel_id"] for s in r["schemalagda"]] == [ids[2], ids[1]]  # köns ordning
    assert r["schemalagda"][0]["lokal_tid"].endswith("08:30+01:00") or r["schemalagda"][0]["lokal_tid"].endswith("08:30+02:00")
    oversikt = klient.get("/api/publicering").json()
    assert len({s["datum"] for s in oversikt["kalender"]}) == 3
    assert {a["status"] for a in klient.get("/api/artiklar").json()["artiklar"]} == {"schemalagd"}


def test_databasen_tillater_aldrig_tva_publiceringar_samma_dag(klient):
    from app import db

    a, b = godkand_artikel(klient, "fukt"), godkand_artikel(klient, "radon")
    with db._conn() as c:
        for artikel_id in (a, b):
            try:
                c.execute("INSERT INTO publiceringar (artikel_id, datum, tid_utc, status, godkand_hash, skapad, uppdaterad) "
                          "VALUES (?, '2030-01-01', '2030-01-01T06:00:00+00:00', 'planerad', 'x', 'n', 'n')", (artikel_id,))
            except sqlite3.IntegrityError:
                break
        else:
            pytest.fail("två publiceringar samma dag godtogs")


def test_flytta_datum_och_avboka(klient):
    a, b = godkand_artikel(klient, "fukt"), godkand_artikel(klient, "radon")
    start = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": start.isoformat()})
    kal = {s["artikel_id"]: s for s in klient.get("/api/publicering").json()["kalender"]}
    # Upptagen dag nekas.
    r = klient.post(f"/api/publicering/{kal[a]['id']}/flytta", json={"datum": kal[b]["datum"]})
    assert r.status_code == 409
    ny = (start + timedelta(days=10)).isoformat()
    assert klient.post(f"/api/publicering/{kal[a]['id']}/flytta", json={"datum": ny}).json()["datum"] == ny
    assert klient.post(f"/api/artiklar/{b}/avboka").json() == {"status": "godkand"}
    kal = klient.get("/api/publicering").json()["kalender"]
    assert [(s["artikel_id"], s["datum"]) for s in kal] == [(a, ny)]


def test_redigering_stoppar_schemalaggningen(klient):
    a = godkand_artikel(klient)
    klient.post("/api/publicering/schemalagg", json={"startdatum": imorgon().isoformat()})
    innehall = klient.get(f"/api/artiklar/{a}").json()["innehall"]
    # Samma innehåll påverkar inget.
    assert klient.put(f"/api/artiklar/{a}/innehall", json=innehall).json()["status"] == "schemalagd"
    r = klient.put(f"/api/artiklar/{a}/innehall", json=dict(innehall, titel="Ändrad rubrik"))
    assert r.json()["status"] == "granskas"
    assert klient.get("/api/publicering").json()["kalender"] == []
    assert [h["handling"] for h in klient.get(f"/api/artiklar/{a}").json()["historik"]][:2] == ["godkannande_upphavt", "avbokad"]


def test_andring_direkt_i_databasen_stoppas_vid_publicering(klient, wp, monkeypatch):
    from app import db

    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    a = godkand_artikel(klient)
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    innehall = db.hamta(a)["innehall"]
    db.uppdatera(a, innehall=dict(innehall, html="<p>Smygändring</p>"))
    kor(kl(dag), wp)
    assert db.hamta(a)["status"] == "granskas"
    assert wp.skapade == 0


def test_simulering_skickar_ingenting(klient, wp):
    from app import db

    a = godkand_artikel(klient)
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    kor(kl(dag, 6, 59), wp)  # innan utsatt tid händer inget
    assert db.hamta(a)["status"] == "schemalagd"
    kor(kl(dag), wp)
    assert wp.skapade == 0 and wp.inlagg == {}
    assert db.hamta(a)["status"] == "godkand"
    assert klient.get("/api/publicering").json()["kalender"][0]["status"] == "simulerad"


def test_skarp_publicering_med_seo_falt_och_css_via_plugin(klient, wp, monkeypatch):
    from app import db

    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    a = godkand_artikel(klient)
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    kor(kl(dag), wp)
    artikel = db.hamta(a)
    assert artikel["status"] == "publicerad" and artikel["wp_post_id"] == 100
    post = wp.inlagg[100]
    assert post["status"] == "publish"
    assert post["meta"]["_yoast_wpseo_title"] == artikel["innehall"]["meta_titel"]
    assert post["meta"]["_kh_studio_id"] == f"artikel-{a}"
    assert post["content"].startswith("<!-- wp:html -->") and "<style>" not in post["content"]
    logg = klient.get("/api/publicering").json()["logg"]
    assert logg[0]["handling"] == "publicerad" and "klimathemmet.se" in logg[0]["kommentar"]
    # Ny körning gör ingenting mer.
    kor(kl(dag, 12), wp)
    assert wp.skapade == 1


def test_omstart_mitt_i_publicering_publicerar_inte_dubbelt(klient, wp, monkeypatch):
    from app import db, publicering

    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    a = godkand_artikel(klient)
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    kor(kl(dag), wp)
    # Simulera att servern dog efter att WordPress publicerat men innan databasen hann uppdateras.
    with db._conn() as c:
        c.execute("UPDATE publiceringar SET status = 'publicerar'")
        c.execute("UPDATE artiklar SET status = 'schemalagd', wp_post_id = NULL")
    publicering.init()  # som vid omstart
    kor(kl(dag, 7, 2), wp)
    assert wp.skapade == 1 and len(wp.inlagg) == 1
    assert db.hamta(a)["status"] == "publicerad"


def test_annat_inlagg_med_samma_adress_stoppar_publiceringen(klient, wp, monkeypatch):
    from app import db

    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    a = godkand_artikel(klient)
    slug = db.hamta(a)["innehall"]["slug"]
    wp.inlagg[50] = {"id": 50, "slug": slug, "status": "publish", "meta": {}}
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    for minut in (1, 7, 20):  # tre försök med väntetid emellan
        kor(kl(dag, 7, minut), wp)
    assert wp.skapade == 0
    assert db.hamta(a)["status"] == "godkand"
    assert klient.get("/api/publicering").json()["kalender"][0]["status"] == "fel"


def test_tillfalligt_fel_forsoks_igen(klient, wp, monkeypatch):
    from app import db

    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    a = godkand_artikel(klient)
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    wp.fel_kvar = 1
    kor(kl(dag), wp)
    assert db.hamta(a)["status"] == "schemalagd"
    kor(kl(dag, 7, 3), wp)  # för tidigt för nästa försök
    assert db.hamta(a)["status"] == "schemalagd"
    kor(kl(dag, 7, 10), wp)
    assert db.hamta(a)["status"] == "publicerad"


def test_paus_och_missad_dag(klient, wp, monkeypatch):
    from app import db

    ny_installning(monkeypatch, STUDIO_PUBLICERING="skarp")
    a = godkand_artikel(klient)
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    klient.post("/api/publicering/paus", json={"pausad": True})
    kor(kl(dag), wp)
    assert db.hamta(a)["status"] == "schemalagd" and wp.skapade == 0
    # Pausen hävs först nästa dag: artikeln publiceras inte i efterhand.
    klient.post("/api/publicering/paus", json={"pausad": False})
    kor(kl(dag + timedelta(days=1)), wp)
    assert wp.skapade == 0
    assert db.hamta(a)["status"] == "godkand"
    assert klient.get("/api/publicering").json()["kalender"][0]["status"] == "missad"


def test_utkastlage_skapar_bara_utkast(klient, wp, monkeypatch):
    from app import db

    ny_installning(monkeypatch, STUDIO_PUBLICERING="utkast")
    a = godkand_artikel(klient)
    dag = imorgon()
    klient.post("/api/publicering/schemalagg", json={"startdatum": dag.isoformat()})
    kor(kl(dag), wp)
    assert wp.inlagg[100]["status"] == "draft"
    assert db.hamta(a)["status"] == "godkand"


def test_svensk_tid_och_sommartid():
    from app import publicering

    vinter = publicering.lokal_tid(date(2027, 1, 15), STHLM_7).astimezone(timezone.utc)
    sommar = publicering.lokal_tid(date(2027, 7, 15), STHLM_7).astimezone(timezone.utc)
    assert (vinter.hour, sommar.hour) == (6, 5)
    # Natten när klockan ställs fram: 07:00 finns och blir 05:00 UTC.
    assert publicering.lokal_tid(date(2027, 3, 28), STHLM_7).astimezone(timezone.utc).hour == 5


def test_kontrollera_wordpress(klient, wp):
    from app import wordpress

    async def kontroll():
        async with wordpress.WordPress(transport=httpx.MockTransport(wp)) as w:
            return await w.kontrollera()

    r = asyncio.run(kontroll())
    assert r["seo_plugin"] == ["yoast"] and r["studio_plugin"] and r["kan_publicera"] and r["problem"] == []
    wp.plugin = False
    r = asyncio.run(kontroll())
    assert any("Tilläggspluginet" in p for p in r["problem"])


def test_sakerhetskopia(klient, miljo):
    from app import publicering

    godkand_artikel(klient)
    publicering.daglig_sakerhetskopia()
    filer = list((miljo / "backups").glob("studio-*.db"))
    assert len(filer) == 1
    with sqlite3.connect(filer[0]) as c:
        assert c.execute("SELECT COUNT(*) FROM artiklar").fetchone()[0] == 1
    r = klient.get("/api/sakerhetskopia")
    assert r.status_code == 200 and r.content.startswith(b"SQLite format 3")
