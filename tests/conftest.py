import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture()
def klient(tmp_path, monkeypatch):
    """Testklient med en tom databas och utan riktiga nycklar (demotext används)."""
    monkeypatch.setenv("STUDIO_DB", str(tmp_path / "test.db"))
    for namn in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "WP_APP_PASSWORD", "STUDIO_PASSWORD", "STUDIO_USER"):
        monkeypatch.delenv(namn, raising=False)
    monkeypatch.setenv("STUDIO_SECRETS_DIR", str(tmp_path / "secrets"))
    monkeypatch.setenv("STUDIO_CLAUDE_EFFORT", "high")

    import importlib

    from app import config, db

    importlib.reload(config)
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    from app import jobb, main

    monkeypatch.setattr(jobb, "settings", config.settings)
    monkeypatch.setattr(main, "settings", config.settings)
    from fastapi.testclient import TestClient

    with TestClient(main.app) as c:
        yield c
