import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def miljo(tmp_path, monkeypatch):
    """Tom databas och datamapp, utan riktiga nycklar (demotext används) och utan schemaläggare."""
    for namn in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "WP_URL", "WP_USER", "WP_APP_PASSWORD", "STUDIO_PASSWORD",
                 "STUDIO_USER", "STUDIO_SESSION_SECRET", "STUDIO_MILJO", "STUDIO_PUBLICERING"):
        monkeypatch.delenv(namn, raising=False)
    monkeypatch.setenv("STUDIO_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("STUDIO_DATA", str(tmp_path))
    monkeypatch.setenv("STUDIO_CLAUDE_EFFORT", "high")
    monkeypatch.setenv("STUDIO_SCHEMALAGGARE", "av")

    import importlib

    from app import config, db

    importlib.reload(config)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    return tmp_path


def ny_installning(monkeypatch, **env):
    """Byter inställningar mitt i ett test."""
    from app import config

    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(config, "settings", config.Settings())


@pytest.fixture()
def klient(miljo):
    from fastapi.testclient import TestClient

    from app import main

    # Gränssnittet skickar alltid X-Studio: 1 på ändrande anrop (skydd mot CSRF).
    with TestClient(main.app, headers={"X-Studio": "1"}) as c:
        yield c
