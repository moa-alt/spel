"""Publiceringskalendern, WordPress-fälten, återförsök och schemaläggarlåset. WordPress är alltid mockad."""

import subprocess
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from conftest import ny_installning
from test_publicering import FalskWordPress, godkand_artikel, kl, kor

SOMMAR = date(2027, 6, 15)
VINTER = date(2027, 1, 15)


@pytest.fixture()
def wp(klient, monkeypatch):
    ny_installning(monkeypatch, WP_URL="https://klimathemmet.se", WP_USER="moa", WP_APP_PASSWORD="abcd efgh ijkl",
                   STUDIO_PUBLICERING="skarp")
    return FalskWordPress()


def schemalagg_exakt(klient, aid, dag, klockslag):
    return klient.post("/api/publicering/schemalagg",
                       json={"startdatum": dag.isoformat(), "klockslag": klockslag, "artikel_ids": [aid], "exakt": True})


def slot(klient, aid):
    return next(p for p in klient.get("/api/publicering").json()["kalender"] if p["artikel_id"] == aid)


def test_exakt_datum_och_klockslag_i_svensk_tid(klient):
    a = godkand_artikel(klient, "värmepump villa")
    b = godkand_artikel(klient, "bergvärme kostnad")
    assert schemalagg_exakt(klient, a, SOMMAR, "14:30").status_code == 200
    s = slot(klient, a)
    assert s["datum"] == "2027-06-15" and s["tid_utc"] == "2027-06-15T12:30:00+00:00" and s["lokal_tid"] == "2027-06-15T14:30+02:00"
    # Vintertid: UTC+1.
    assert schemalagg_exakt(klient, b, VINTER, "14:30").status_code == 200
    assert slot(klient, b)["tid_utc"] == "2027-01-15T13:30:00+00:00"


def test_exakt_schemalaggning_flyttar_aldrig_till_en_annan_dag(klient):
    a = godkand_artikel(klient, "värmepump villa")
    b = godkand_artikel(klient, "bergvärme kostnad")
    schemalagg_exakt(klient, a, SOMMAR, "07:00")
    r = schemalagg_exakt(klient, b, SOMMAR, "18:00")
    assert r.status_code == 409 and "redan en publicering" in r.json()["detail"]
    r = schemalagg_exakt(klient, b, date.today() - timedelta(days=1), "07:00")
    assert r.status_code == 409
    r = klient.post("/api/publicering/schemalagg", json={"startdatum": SOMMAR.isoformat(), "artikel_ids": [a, b], "exakt": True})
    assert r.status_code == 409


def test_flytta_med_klockslag_och_avbryt(klient):
    from app import db

    a = godkand_artikel(klient)
    schemalagg_exakt(klient, a, SOMMAR, "07:00")
    s = slot(klient, a)
    r = klient.post(f"/api/publicering/{s['id']}/flytta", json={"datum": "2027-06-20", "klockslag": "09:15"})
    assert r.status_code == 200 and r.json()["lokal_tid"] == "2027-06-20T09:15+02:00"
    assert db.hamta(a)["publiceras_at"] == "2027-06-20T07:15:00+00:00"
    assert klient.post(f"/api/artiklar/{a}/avboka").status_code == 200
    assert db.hamta(a)["status"] == "godkand" and db.hamta(a)["publiceras_at"] is None
    assert all(p["artikel_id"] != a for p in klient.get("/api/publicering").json()["kalender"])


def test_oversikten_visar_alla_statusar(klient):
    godkand_artikel(klient, "värmepump villa")
    klient.post("/api/artiklar", json={"huvudsokord": "fukt i källare"})
    statusar = {a["status"] for a in klient.get("/api/publicering").json()["artiklar"]}
    assert statusar == {"godkand", "utkast"}


def test_publicering_skickar_kategori_datum_fokusord_och_sparar_adressen(klient, wp):
    from app import db

    a = godkand_artikel(klient)
    klient.post(f"/api/artiklar/{a}/aterkalla")
    innehall = klient.get(f"/api/artiklar/{a}").json()["innehall"] | {"kategori_id": 10}
    klient.put(f"/api/artiklar/{a}/innehall", json=innehall)
    klient.post(f"/api/artiklar/{a}/godkann", json={"bekraftad": True, "fakta_verifierade": True})
    dag = date.today() + timedelta(days=2)
    schemalagg_exakt(klient, a, dag, "06:45")
    kor(kl(dag, 6, 46), wp)
    post = wp.inlagg[100]
    assert post["categories"] == [10] and post["status"] == "publish"
    from app.publicering import lokal_tid

    assert post["date_gmt"] == lokal_tid(dag, time(6, 45)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    assert post["meta"]["_yoast_wpseo_focuskw"] == "ventilation i gamla hus"
    artikel = db.hamta(a)
    assert artikel["wp_post_id"] == 100 and artikel["wp_lank"] == "https://klimathemmet.se/ventilation-i-gamla-hus/"
    oversikt = next(x for x in klient.get("/api/publicering").json()["artiklar"] if x["id"] == a)
    assert oversikt["status"] == "publicerad" and oversikt["wp_lank"].endswith("/ventilation-i-gamla-hus/")


def test_permanent_fel_forsoks_inte_igen_men_tillfalliga_gor(klient, wp, monkeypatch):
    from app import db

    a = godkand_artikel(klient)
    dag = date.today() + timedelta(days=2)
    schemalagg_exakt(klient, a, dag, "07:00")
    original = wp.__call__

    def avvisa(request):
        if request.method == "POST" and request.url.path.endswith("/wp/v2/posts"):
            return httpx.Response(400, json={"code": "rest_invalid_param"})
        return original(request)
    kor(kl(dag), avvisa)
    s = slot(klient, a)
    assert s["status"] == "fel" and s["forsok"] == 0 and "inte över av sig självt" in s["fel"]
    assert db.hamta(a)["status"] == "godkand" and wp.skapade == 0
    # Ett serverfel försöks igen senare.
    b = godkand_artikel(klient, "bergvärme kostnad")
    dag2 = dag + timedelta(days=1)
    schemalagg_exakt(klient, b, dag2, "07:00")
    wp.fel_kvar = 1
    kor(kl(dag2), wp)
    s = slot(klient, b)
    assert s["status"] == "planerad" and s["forsok"] == 1
    kor(kl(dag2) + timedelta(minutes=6), wp)
    assert slot(klient, b)["status"] == "publicerad"


def test_bara_en_schemalaggare_haller_laset(klient):
    from app import publicering

    nu = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    assert publicering.ta_las(nu, "server-a")
    assert not publicering.ta_las(nu + timedelta(seconds=30), "server-b")
    assert publicering.ta_las(nu + timedelta(seconds=60), "server-a")  # förnyas
    # Låset går ut om ägaren slutar förnya det (t.ex. kraschar).
    assert publicering.ta_las(nu + timedelta(seconds=60 + publicering.LAS_SEKUNDER + 1), "server-b")
    assert not publicering.ta_las(nu + timedelta(seconds=200), "server-a")
    publicering.slapp_las("server-b")
    assert publicering.ta_las(nu + timedelta(seconds=201), "server-a")


def test_kvalitetsfel_hindrar_schemalaggning(klient):
    a = godkand_artikel(klient, "värmepump villa")
    # En ny artikel med samma huvudsökord gör den godkända artikeln till en kannibal.
    klient.post("/api/artiklar", json={"huvudsokord": "värmepump villa"})
    r = schemalagg_exakt(klient, a, SOMMAR, "07:00")
    assert r.status_code == 409 and "kritiska fel" in r.json()["detail"]


def test_inga_hemligheter_i_git():
    rot = Path(__file__).resolve().parent.parent
    try:
        filer = subprocess.run(["git", "ls-files"], cwd=rot, capture_output=True, text=True, check=True).stdout.split()
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("inte ett git-arkiv")
    assert ".env" not in filer and not any(f.endswith("/.env") for f in filer)
    import re

    monster = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}|sk-(proj-)?[A-Za-z0-9_-]{32,}|^(WP_APP_PASSWORD|STUDIO_PASSWORD|STUDIO_SESSION_SECRET)=\S+", re.M)
    for f in filer:
        sokvag = rot / f
        if sokvag.suffix in (".png", ".webp", ".jpg", ".zip", ".db") or not sokvag.is_file():
            continue
        text = sokvag.read_text(encoding="utf-8", errors="ignore")
        assert not monster.search(text), f"{f} ser ut att innehålla en hemlighet"
