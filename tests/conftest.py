import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Testerna ska aldrig få dina riktiga inställningar: .env läses inte in, och alla variabler som rör
# nycklar, WordPress och publicering tas bort, även för servrarna som webbläsartesterna startar.
KANSLIGA = (
    "ANTHROPIC_API_KEY", "ANTHROPIC_API_KEY_FILE", "ANTHROPIC_BASE_URL", "OPENAI_API_KEY", "OPENAI_API_KEY_FILE",
    "WP_URL", "WP_USER", "WP_APP_PASSWORD", "WP_APP_PASSWORD_FILE",
    "STUDIO_USER", "STUDIO_PASSWORD", "STUDIO_PASSWORD_FILE", "STUDIO_SESSION_SECRET", "STUDIO_SESSION_SECRET_FILE",
    "STUDIO_MILJO", "STUDIO_PUBLICERING", "STUDIO_DB", "STUDIO_DATA", "STUDIO_SECRETS_DIR", "STUDIO_ENV_FIL",
    "STUDIO_CLAUDE_MODEL", "STUDIO_CLAUDE_EFFORT", "STUDIO_SCHEMALAGGARE",
)
for _namn in KANSLIGA:
    os.environ.pop(_namn, None)
os.environ["STUDIO_DOTENV"] = "0"
os.environ["STUDIO_SECRETS_DIR"] = str(Path(__file__).resolve().parent / ".inga-hemligheter")
TILLATNA_VARDAR = {"127.0.0.1", "localhost", "testserver"}


@pytest.fixture(autouse=True)
def inget_natverk(monkeypatch):
    """Spärr: inget test får ansluta till något utanför den här datorn, till exempel klimathemmet.se."""
    import httpx

    orginal = httpx.AsyncHTTPTransport.handle_async_request
    orginal_sync = httpx.HTTPTransport.handle_request

    def kontroll(request):
        if request.url.host not in TILLATNA_VARDAR:
            raise RuntimeError(f"Testet försökte ansluta till {request.url.host}; tester får bara använda lokala låtsas-tjänster.")

    async def sparrad_async(self, request):
        kontroll(request)
        return await orginal(self, request)

    def sparrad(self, request):
        kontroll(request)
        return orginal_sync(self, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", sparrad_async)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", sparrad)


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
