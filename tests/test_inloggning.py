import pytest

from conftest import ny_installning

LOSEN = "ett-langt-hemligt-losenord"


@pytest.fixture()
def skyddad(klient, monkeypatch):
    from app import auth

    auth._misslyckade.clear()
    ny_installning(monkeypatch, STUDIO_USER="moa", STUDIO_PASSWORD=LOSEN, STUDIO_SESSION_SECRET="x" * 40)
    return klient


def test_utan_inloggning_nekas_allt_utom_inloggningssidan(skyddad):
    assert skyddad.get("/api/artiklar").status_code == 401
    r = skyddad.get("/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert skyddad.get("/login").status_code == 200
    assert skyddad.get("/healthz").json() == {"ok": True}


def test_inloggning_ger_saker_sessionskaka(skyddad):
    assert skyddad.post("/api/login", json={"anvandare": "moa", "losenord": "fel"}).status_code == 401
    r = skyddad.post("/api/login", json={"anvandare": "moa", "losenord": LOSEN})
    assert r.status_code == 200
    kaka = r.headers["set-cookie"].lower()
    assert "httponly" in kaka and "samesite=strict" in kaka and LOSEN not in kaka
    assert skyddad.get("/api/artiklar").status_code == 200
    skyddad.post("/api/logout")
    skyddad.cookies.clear()
    assert skyddad.get("/api/artiklar").status_code == 401


def test_forfalskad_eller_gammal_session_godtas_inte(skyddad, monkeypatch):
    from app import auth

    skyddad.cookies.set(auth.KAKA, "bW9hfDk5OTk5OTk5OTk.falsk-signatur")
    assert skyddad.get("/api/artiklar").status_code == 401
    monkeypatch.setattr(auth, "SESSION_SEKUNDER", -10)
    skyddad.cookies.set(auth.KAKA, auth.skapa_session("moa"))
    assert skyddad.get("/api/artiklar").status_code == 401


def test_for_manga_forsok_blockeras(skyddad):
    for _ in range(5):
        skyddad.post("/api/login", json={"anvandare": "moa", "losenord": "fel"})
    r = skyddad.post("/api/login", json={"anvandare": "moa", "losenord": LOSEN})
    assert r.status_code == 429


def test_andrande_anrop_kraver_sakerhetsrubrik(klient):
    r = klient.post("/api/artiklar", json={"huvudsokord": "fukt"}, headers={"X-Studio": ""})
    assert r.status_code == 403
    assert klient.post("/api/artiklar", json={"huvudsokord": "fukt"}).status_code == 201


def test_sakerhetsrubriker(klient):
    h = klient.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"
    assert klient.get("/api/artiklar").headers["cache-control"] == "no-store"


def test_produktion_startar_inte_utan_sakerhetsinstallningar(miljo, monkeypatch):
    from app import config

    ny_installning(monkeypatch, STUDIO_MILJO="produktion")
    with pytest.raises(RuntimeError, match="STUDIO_USER"):
        config.settings.kontrollera_produktion()
    ny_installning(monkeypatch, STUDIO_USER="moa", STUDIO_PASSWORD=LOSEN, STUDIO_SESSION_SECRET="kort")
    with pytest.raises(RuntimeError, match="SESSION_SECRET"):
        config.settings.kontrollera_produktion()
    ny_installning(monkeypatch, STUDIO_SESSION_SECRET="x" * 40)
    config.settings.kontrollera_produktion()


def test_produktion_skickar_http_till_https(klient, monkeypatch):
    ny_installning(monkeypatch, STUDIO_MILJO="produktion", STUDIO_USER="moa", STUDIO_PASSWORD=LOSEN,
                   STUDIO_SESSION_SECRET="x" * 40)
    r = klient.get("/login", headers={"X-Forwarded-Proto": "http"}, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"].startswith("https://")
    assert "strict-transport-security" in klient.get("/login", headers={"X-Forwarded-Proto": "https"}).headers


def test_status_visar_aldrig_hemligheter(klient, monkeypatch):
    ny_installning(monkeypatch, ANTHROPIC_API_KEY="sk-ant-test-hemlig", WP_APP_PASSWORD="abcd efgh")
    svar = klient.get("/api/status").text
    assert "sk-ant" not in svar and "abcd" not in svar and '"ai_text":true' in svar
