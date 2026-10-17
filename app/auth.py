"""Inloggning, sessioner och säkerhetsrubriker.

- Inloggning med användarnamn och lösenord ger en signerad sessionskaka (HttpOnly, SameSite=Strict,
  Secure i produktion). Kakan innehåller inget lösenord.
- Ändrande API-anrop måste skicka rubriken X-Studio: 1. En annan webbplats kan inte skicka den
  utan att webbläsaren först frågar servern, och servern tillåter inga sådana anrop (skydd mot CSRF).
- Högst MAX_FORSOK misslyckade inloggningar per IP-adress och kvart.
"""

import base64
import hashlib
import hmac
import logging
import secrets
import time
from collections import defaultdict, deque

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from . import config

log = logging.getLogger("studio.auth")

KAKA = "kh_session"
SESSION_SEKUNDER = 14 * 24 * 3600
MAX_FORSOK = 5
FORSOK_FONSTER = 15 * 60
OPPNA_SIDOR = ("/login", "/api/login", "/healthz", "/static/")
ANDRANDE_METODER = {"POST", "PUT", "PATCH", "DELETE"}

# Används bara i utveckling utan STUDIO_SESSION_SECRET; sessioner försvinner då vid omstart.
_tillfallig_nyckel = secrets.token_bytes(32)
_misslyckade: dict[str, deque] = defaultdict(deque)


def _nyckel() -> bytes:
    s = config.settings.session_secret
    return hashlib.sha256(s.encode()).digest() if s else _tillfallig_nyckel


def _signera(data: str) -> str:
    return base64.urlsafe_b64encode(hmac.new(_nyckel(), data.encode(), hashlib.sha256).digest()).decode().rstrip("=")


def skapa_session(anvandare: str) -> str:
    data = f"{anvandare}|{int(time.time()) + SESSION_SEKUNDER}"
    return base64.urlsafe_b64encode(data.encode()).decode() + "." + _signera(data)


def las_session(varde: str | None) -> str | None:
    if not varde or "." not in varde:
        return None
    del1, signatur = varde.rsplit(".", 1)
    try:
        data = base64.urlsafe_b64decode(del1.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(signatur, _signera(data)):
        return None
    anvandare, _, utgar = data.partition("|")
    if not utgar.isdigit() or int(utgar) < time.time() or anvandare != config.settings.studio_user:
        return None
    return anvandare


def klient_ip(request: Request) -> str:
    return request.client.host if request.client else "okand"


def for_manga_forsok(ip: str) -> bool:
    tider = _misslyckade[ip]
    grans = time.monotonic() - FORSOK_FONSTER
    while tider and tider[0] < grans:
        tider.popleft()
    return len(tider) >= MAX_FORSOK


def kontrollera_losenord(ip: str, anvandare: str, losenord: str) -> bool:
    s = config.settings
    ok = hmac.compare_digest(anvandare.encode(), s.studio_user.encode()) & hmac.compare_digest(
        losenord.encode(), s.studio_password.encode()
    )
    if ok:
        _misslyckade.pop(ip, None)
    else:
        _misslyckade[ip].append(time.monotonic())
        log.warning("Misslyckad inloggning från %s", ip)
    return ok


def satt_kaka(resp: Response, anvandare: str) -> None:
    resp.set_cookie(
        KAKA, skapa_session(anvandare), max_age=SESSION_SEKUNDER, httponly=True,
        secure=config.settings.produktion, samesite="strict", path="/",
    )


def rensa_kaka(resp: Response) -> None:
    resp.delete_cookie(KAKA, path="/")


CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data: https:; connect-src 'self'; "
    "frame-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def sakerhetsrubriker(resp: Response) -> None:
    resp.headers.setdefault("Content-Security-Policy", CSP)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    if config.settings.produktion:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")


async def middleware(request: Request, call_next):
    s = config.settings
    path = request.url.path

    # I produktion ligger appen bakom en proxy som avslutar HTTPS; vanlig HTTP skickas vidare till HTTPS.
    if s.produktion and request.headers.get("x-forwarded-proto", "https") != "https":
        return RedirectResponse(str(request.url.replace(scheme="https")), status_code=308)

    if path.startswith("/api/") and request.method in ANDRANDE_METODER and request.headers.get("x-studio") != "1":
        resp = JSONResponse({"detail": "Saknar säkerhetsrubrik."}, status_code=403)
        sakerhetsrubriker(resp)
        return resp

    if s.inloggning_aktiv and not path.startswith(OPPNA_SIDOR) and not las_session(request.cookies.get(KAKA)):
        if path.startswith("/api/"):
            resp = JSONResponse({"detail": "Du är inte inloggad."}, status_code=401)
        else:
            resp = RedirectResponse("/login", status_code=303)
        sakerhetsrubriker(resp)
        return resp

    resp = await call_next(request)
    sakerhetsrubriker(resp)
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp
