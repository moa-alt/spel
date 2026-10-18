"""Testerna ska vara oberoende av inställningarna på datorn där de körs."""

import importlib
import os

import httpx
import pytest

ENV_MED_SAJT = "WP_URL=https://klimathemmet.se\nSTUDIO_PUBLICERING=skarp\n"


def test_env_fil_lases_inte_in_i_testerna(miljo, monkeypatch):
    """Återskapar felet där en .env med WP_URL fick testet att kartlägga den riktiga sajten."""
    from app import config

    env_fil = miljo / ".env"
    env_fil.write_text(ENV_MED_SAJT, encoding="utf-8")
    monkeypatch.setenv("STUDIO_ENV_FIL", str(env_fil))
    importlib.reload(config)
    assert config.settings.wp_url == "" and config.settings.publicering == "simulering"
    assert "WP_URL" not in os.environ


def test_saknad_adress_ger_begripligt_fel_aven_med_env_fil(miljo, monkeypatch):
    from fastapi.testclient import TestClient

    from app import config, main

    env_fil = miljo / ".env"
    env_fil.write_text(ENV_MED_SAJT, encoding="utf-8")
    monkeypatch.setenv("STUDIO_ENV_FIL", str(env_fil))
    importlib.reload(config)
    with TestClient(main.app, headers={"X-Studio": "1"}) as klient:
        r = klient.post("/api/seokarta/kartlagg")
        assert r.status_code == 502
        assert r.json()["detail"].startswith("WP_URL är inte satt")
        karta = klient.get("/api/seokarta").json()
        assert karta["wp_url"] == "" and karta["noder"] == []


def test_env_fil_lases_in_utanfor_testerna(miljo, monkeypatch):
    """I vanlig drift (STUDIO_DOTENV inte satt till 0) läses .env in som tidigare."""
    from app import config

    env_fil = miljo / ".env"
    env_fil.write_text("WP_URL=https://exempel.se\n", encoding="utf-8")
    monkeypatch.setenv("STUDIO_ENV_FIL", str(env_fil))
    monkeypatch.setenv("STUDIO_DOTENV", "1")
    try:
        importlib.reload(config)
        assert config.settings.wp_url == "https://exempel.se"
    finally:
        os.environ.pop("WP_URL", None)
        monkeypatch.setenv("STUDIO_DOTENV", "0")
        importlib.reload(config)


def test_natverksparr_stoppar_anrop_till_riktiga_sajter():
    with pytest.raises(RuntimeError, match="klimathemmet.se"):
        httpx.get("https://klimathemmet.se/wp-json/")
