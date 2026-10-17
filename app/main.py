"""Klimathemmet AI Studio – webbserver.

Starta: uvicorn app.main:app --reload
"""

import base64
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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


@app.get("/api/status")
def status():
    return settings.status()


@app.post("/api/generera")
def generera(req: GenerateRequest):
    # Steg 2 ersätter demoartikeln med AI-genererad text.
    return build_demo_article(req.huvudsokord, req.sekundara_sokord)


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
