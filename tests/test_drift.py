"""Driftsäkerhet: säkerhetskopia och återställning av databas och bilder, kontrollskriptet och produktionsstart."""

import io
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from conftest import ny_installning

ROT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def studio(miljo, monkeypatch):
    """En studio vars databas ligger i datamappen, precis som /data/studio.db på servern."""
    from fastapi.testclient import TestClient

    from app import db, main

    monkeypatch.setattr(db, "DB_PATH", miljo / "studio.db")
    ny_installning(monkeypatch, STUDIO_BILDTJANST="test")
    with TestClient(main.app, headers={"X-Studio": "1"}) as k:
        yield k, miljo


def _artikel_med_bild(k):
    from app import db

    a = k.post("/api/artiklar", json={"huvudsokord": "värmepump i villa"}).json()
    k.post(f"/api/artiklar/{a['id']}/generera")
    for _ in range(200):
        if db.hamta(a["id"])["status"] == "granskas":
            break
        time.sleep(0.02)
    k.post(f"/api/artiklar/{a['id']}/bilder/generera", json={"bild_ids": [1], "bekraftad": True})
    for _ in range(200):
        b = db.hamta(a["id"])["innehall"]["bilder"][0]
        if b.get("status") == "klar":
            return a["id"], b["fil"]
        time.sleep(0.02)
    raise AssertionError("bilden blev aldrig klar")


def test_komplett_kopia_och_aterstallning(studio):
    from app import bilder, db, publicering, sakerhetskopia

    k, data = studio
    aid, fil = _artikel_med_bild(k)
    arkiv = data / "kopia.tgz"
    info = sakerhetskopia.skapa_arkiv(arkiv)
    assert info["bilder"] == 1
    with tarfile.open(arkiv) as t:
        assert set(t.getnames()) == {"studio.db", "manifest.json", f"bilder/{aid}/{fil}"}
    # Allt försvinner, och en annan instans håller schemaläggarlåset.
    db.ta_bort(aid)
    bilder.ta_bort_filer(aid)
    assert publicering.ta_las(instans="gammal-instans")
    arkiv.rename(data / "aterstall.tgz")
    text = sakerhetskopia.aterstall_vid_start(data)
    assert text.startswith("Återställd")
    assert db.hamta(aid)["huvudsokord"] == "värmepump i villa"
    assert (bilder.mapp(aid) / fil).is_file()
    assert publicering.ta_las(instans="ny-instans"), "låset från kopian ska vara släppt"
    # Det som fanns innan återställningen sparas, och arkivet används bara en gång.
    assert list(data.glob("fore-aterstallning-*/studio.db")) and list(data.glob("aterstall-klar-*.tgz"))
    assert not (data / "aterstall.tgz").exists()
    assert sakerhetskopia.aterstall_vid_start(data) is None


def test_aterstallning_av_bara_databasen_behaller_bilderna(studio):
    from app import bilder, db, publicering, sakerhetskopia

    k, data = studio
    aid, fil = _artikel_med_bild(k)
    # En nattlig kopia innehåller bara databasen.
    publicering.sakerhetskopiera(data / "natt.db")
    db.ta_bort(aid)
    with tarfile.open(data / "aterstall.tgz", "w:gz") as t:
        t.add(data / "natt.db", arcname="studio.db")
    assert sakerhetskopia.aterstall_vid_start(data).startswith("Återställd")
    assert db.hamta(aid) is not None and (bilder.mapp(aid) / fil).is_file()


def _arkiv(filer: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for namn, data in filer.items():
            info = tarfile.TarInfo(namn)
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@pytest.mark.parametrize("filer,fel", [
    ({"studio.db": b"x", "../utanfor.txt": b"x"}, "Otillåten"),
    ({"studio.db": b"x", "bilder/1/skript.sh": b"x"}, "Otillåten"),
    ({"manifest.json": b"{}"}, "saknar studio.db"),
    ({"studio.db": b"inte en databas"}, "databas"),
])
def test_felaktigt_arkiv_andrar_ingenting(studio, filer, fel):
    from app import db, sakerhetskopia

    k, data = studio
    aid = k.post("/api/artiklar", json={"huvudsokord": "fukt i källare"}).json()["id"]
    (data / "aterstall.tgz").write_bytes(_arkiv(filer))
    text = sakerhetskopia.aterstall_vid_start(data)
    assert "avbröts och inget ändrades" in text and fel.lower() in text.lower()
    assert db.hamta(aid) is not None
    assert list(data.glob("aterstall-fel-*.tgz")) and not list(data.glob("fore-aterstallning-*"))
    assert not (data / "utanfor.txt").exists() and not (data.parent / "utanfor.txt").exists()


def test_komplett_kopia_kan_laddas_ner_och_kraver_inloggning(studio, monkeypatch):
    k, data = studio
    _artikel_med_bild(k)
    r = k.get("/api/sakerhetskopia/komplett")
    assert r.status_code == 200 and r.headers["content-type"] == "application/gzip"
    with tarfile.open(fileobj=io.BytesIO(r.content)) as t:
        assert "studio.db" in t.getnames() and any(n.startswith("bilder/") for n in t.getnames())
    ny_installning(monkeypatch, STUDIO_USER="moa", STUDIO_PASSWORD="ett-langt-losenord", STUDIO_SESSION_SECRET="x" * 40)
    assert k.get("/api/sakerhetskopia/komplett").status_code == 401


def test_kontrollskriptet_godkanner_konfigurationen():
    r = subprocess.run([sys.executable, "scripts/kontrollera_drift.py"], cwd=ROT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
    assert "✓ Publiceringsläget är simulering" in r.stdout and "✓ Maskinen stängs inte av vid inaktivitet" in r.stdout


def test_fly_konfigurationen_skyddar_driften():
    import tomllib

    fly = tomllib.loads((ROT / "fly.toml").read_text(encoding="utf-8"))
    assert fly["env"]["STUDIO_PUBLICERING"] == "simulering" and fly["env"]["STUDIO_MILJO"] == "produktion"
    assert fly["http_service"]["auto_stop_machines"] == "off" and fly["http_service"]["min_machines_running"] == 1
    assert fly["restart"][0]["policy"] == "always"
    ignorerat = (ROT / ".dockerignore").read_text(encoding="utf-8").split()
    assert {".env", ".env.*", "data", "secrets"} <= set(ignorerat)
    assert "pillow" in (ROT / "requirements.txt").read_text(encoding="utf-8").lower()
    assert "aterstall-vid-start" in (ROT / "scripts" / "starta.sh").read_text(encoding="utf-8")


def test_produktion_startar_inte_utan_skydd_och_loggar_inga_hemligheter(miljo, monkeypatch, caplog):
    from fastapi.testclient import TestClient

    from app import config, main

    monkeypatch.setenv("STUDIO_MILJO", "produktion")
    monkeypatch.setattr(config, "settings", config.Settings())
    with pytest.raises(RuntimeError, match="STUDIO_USER"):
        with TestClient(main.app):
            pass
    hemligt = {"STUDIO_PASSWORD": "ett-langt-hemligt-losenord", "STUDIO_SESSION_SECRET": "s" * 48,
               "WP_APP_PASSWORD": "wp-hemligt-123", "ANTHROPIC_API_KEY": "sk-ant-hemlig-test", "OPENAI_API_KEY": "sk-proj-hemlig-test"}
    ny_installning(monkeypatch, STUDIO_USER="moa", WP_URL="https://klimathemmet.se", WP_USER="studio", **hemligt)
    with caplog.at_level("INFO"), TestClient(main.app, base_url="https://testserver") as k:
        assert k.get("/healthz").json() == {"ok": True}
        assert k.get("/api/artiklar").status_code == 401
        r = k.get("/", headers={"x-forwarded-proto": "http"}, follow_redirects=False)
        assert r.status_code == 308 and r.headers["location"].startswith("https://")
        assert "max-age" in k.get("/login").headers["strict-transport-security"]
    for varde in hemligt.values():
        assert varde not in caplog.text
    assert "simulering" in caplog.text


def test_databasen_ar_i_wal_lage_och_overlever_ny_anslutning(studio):
    k, data = studio
    k.post("/api/artiklar", json={"huvudsokord": "golvvärme"})
    c = sqlite3.connect(data / "studio.db")
    try:
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert c.execute("SELECT huvudsokord FROM artiklar").fetchall() == [("golvvärme",)]
    finally:
        c.close()
