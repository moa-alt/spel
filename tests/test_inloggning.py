import base64


def _auth(user, pwd):
    return {"Authorization": "Basic " + base64.b64encode(f"{user}:{pwd}".encode()).decode()}


def test_studion_kraver_inloggning_nar_losenord_ar_satt(klient, monkeypatch):
    from app import config, main

    monkeypatch.setenv("STUDIO_USER", "moa")
    monkeypatch.setenv("STUDIO_PASSWORD", "hemligt-losenord")
    monkeypatch.setattr(main, "settings", config.Settings())

    for path in ("/", "/api/artiklar", "/static/app.js"):
        r = klient.get(path)
        assert r.status_code == 401 and "WWW-Authenticate" in r.headers
    assert klient.get("/api/artiklar", headers=_auth("moa", "fel")).status_code == 401
    assert klient.get("/api/artiklar", headers=_auth("moa", "hemligt-losenord")).status_code == 200


def test_status_visar_aldrig_hemligheter(klient, monkeypatch):
    from app import config, main

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-hemlig")
    monkeypatch.setattr(main, "settings", config.Settings())
    svar = klient.get("/api/status").text
    assert "sk-ant" not in svar and '"ai_text":true' in svar
