"""Klimathemmet AI Studio – webbserver.

Starta: uvicorn app.main:app --reload
"""

import base64
import json
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .ai_writer import GenereringsFel, generera_artikel
from .config import settings
from .demo import build_demo_article
from .render import wordpress_block

STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Klimathemmet AI Studio", docs_url=None, redoc_url=None)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    """Skyddar hela studion med lösenord när STUDIO_USER/STUDIO_PASSWORD är satta."""
    if settings.studio_user and settings.studio_password:
        ok = False
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                user, _, pwd = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, settings.studio_user) and secrets.compare_digest(
                    pwd, settings.studio_password
                )
            except Exception:
                ok = False
        if not ok:
            return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="AI Studio"'})
    return await call_next(request)


class GenerateRequest(BaseModel):
    huvudsokord: str = Field(min_length=2, max_length=120)
    sekundara_sokord: list[str] = Field(default_factory=list, max_length=15)


class Bild(BaseModel):
    id: int
    alt: str = ""
    bildtext: str = ""
    prompt: str = ""
    url: str | None = None


class Article(BaseModel):
    titel: str
    slug: str
    meta_titel: str
    meta_beskrivning: str
    html: str
    bilder: list[Bild] = Field(default_factory=list)
    huvudsokord: str = ""
    sekundara_sokord: list[str] = Field(default_factory=list)


@app.get("/api/status")
def status():
    return settings.status()


@app.post("/api/generera")
async def generera(req: GenerateRequest):
    """Strömmar förloppet som NDJSON: en JSON-händelse per rad, sist {"typ": "klar", "artikel": ...}."""
    huvudsokord = req.huvudsokord.strip()
    sekundara = [s.strip() for s in req.sekundara_sokord if s.strip()]

    async def handelser():
        if not settings.anthropic_api_key:
            yield {"typ": "klar", "artikel": build_demo_article(huvudsokord, sekundara)}
            return
        try:
            async for h in generera_artikel(huvudsokord, sekundara):
                yield h
        except GenereringsFel as e:
            yield {"typ": "fel", "meddelande": str(e)}

    async def ndjson():
        async for h in handelser():
            yield json.dumps(h, ensure_ascii=False) + "\n"

    return StreamingResponse(ndjson(), media_type="application/x-ndjson", headers={"Cache-Control": "no-store"})


@app.post("/api/exportera")
def exportera(article: Article):
    return {"kod": wordpress_block(article.model_dump())}


@app.post("/api/wordpress")
def skicka_till_wordpress(article: Article):
    raise HTTPException(status_code=501, detail="WordPress-integrationen byggs i ett senare steg.")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
