"""En liten låtsas-WordPress med artiklar, sidor, kategorier och länkar, för tester.

Sajten har tillstånd: inlägg kan läsas i redigeringsläge (raw) och uppdateras, och varje
uppdatering ändrar modified_gmt precis som i WordPress. Alla skrivningar loggas i .skrivningar.
"""

import json

import httpx


class FalskSajt:
    def __init__(self, bas: str):
        self.bas = bas
        self.skrivningar: list[tuple[str, int]] = []
        self.objekt = {"posts": {}, "pages": {}}
        for d in self._poster():
            self.objekt["posts"][d["id"]] = d
        for d in self._sidor():
            self.objekt["pages"][d["id"]] = d
        self.kategorier = [
            {"id": 10, "link": f"{bas}/kategori/ventilation/", "name": "Ventilation", "slug": "ventilation", "count": 4, "description": ""},
            {"id": 11, "link": f"{bas}/kategori/fukt/", "name": "Fukt", "slug": "fukt", "count": 1, "description": ""},
        ]

    def _a(self, slug, text):
        return f'<a href="{self.bas}/{slug}/">{text}</a>'

    def _post(self, pid, slug, titel, html, kategori=None):
        d = {"id": pid, "link": f"{self.bas}/{slug}/", "slug": slug, "title": {"rendered": titel}, "raw": html,
             "modified_gmt": "2026-09-01T08:00:00", "status": "publish",
             "yoast_head_json": {"title": titel, "description": "" if pid == 3 else f"Om {titel.lower()}"}}
        if kategori is not None:
            d["categories"] = [kategori]
        return d

    def _poster(self):
        a = self._a
        return [
            self._post(1, "ventilation-i-gamla-hus", "Ventilation i gamla hus",
                       f"<h2>Självdrag</h2><p>Läs om {a('fukt-i-kallare', 'fukt i källaren')} och {a('ftx-ventilation', 'FTX')}.</p>"
                       f"<p>{a('borttagen-sida', 'en gammal guide')} och {a('kategori/ventilation', 'allt om ventilation')}</p>", 10),
            self._post(2, "ftx-ventilation", "FTX-ventilation i villa",
                       f"<p>Se {a('ventilation-i-gamla-hus', 'ventilation i äldre hus')}.</p>"
                       "<p>Ett vanligt problem är fukt i källare när ventilationen inte räcker till i äldre hus.</p>", 10),
            self._post(3, "fukt-i-kallare", "Fukt i källare", f"<p>Mer om {a('ventilation-i-gamla-hus', 'ventilation')}.</p>", 11),
            self._post(4, "ventilation-gamla-hus-tips", "Ventilation i gamla hus – tips",
                       "<p>Tips utan länkar.</p>"
                       "<p>Många som renoverar funderar på FTX-ventilation, men i ett gammalt hus fungerar självdrag &amp; vädring ofta bättre.</p>", 10),
            self._post(5, "fran-ventilation-till-ftx", "Från självdrag till FTX-ventilation", f"<p>{a('ventilation-i-gamla-hus', 'pelaren')}</p>", 10),
        ]

    def _sidor(self):
        return [self._post(20, "offert-ventilation", "Offert på ventilation", f"<p>{self._a('ftx-ventilation', 'FTX')}</p>")]

    # ---------- Hjälp för tester ----------

    def raw(self, pid, typ="posts"):
        return self.objekt[typ][pid]["raw"]

    def redigera_manuellt(self, pid, ny_raw, typ="posts"):
        """Som om någon redigerat inlägget direkt i WordPress."""
        o = self.objekt[typ][pid]
        o["raw"] = ny_raw
        o["modified_gmt"] = "2026-10-01T12:00:00"

    # ---------- REST API ----------

    def _offentlig(self, o):
        d = {k: v for k, v in o.items() if k != "raw"}
        d["content"] = {"rendered": o["raw"]}
        return d

    def svar(self, metod: str, path: str, params: dict, body: bytes | None) -> tuple[int, object, dict]:
        if metod == "HEAD":
            return (404 if "borttagen" in path else 200), None, {}
        if path == "/wp-json/wp/v2/categories":
            return (200, self.kategorier, {"X-WP-TotalPages": "1"}) if params.get("page") == "1" else (400, {}, {})
        for typ in ("posts", "pages"):
            if path == f"/wp-json/wp/v2/{typ}":
                if params.get("page") != "1":
                    return 400, {"code": "rest_post_invalid_page_number"}, {}
                return 200, [self._offentlig(o) for o in self.objekt[typ].values()], {"X-WP-TotalPages": "1"}
            if path.startswith(f"/wp-json/wp/v2/{typ}/"):
                pid = int(path.rsplit("/", 1)[1])
                o = self.objekt[typ].get(pid)
                if o is None:
                    return 404, {"code": "rest_post_invalid_id"}, {}
                if metod == "POST":
                    data = json.loads(body or b"{}")
                    if "content" in data:
                        o["raw"] = data["content"]
                        o["modified_gmt"] = f"2026-10-17T10:00:{len(self.skrivningar):02d}"
                        self.skrivningar.append((typ, pid))
                d = self._offentlig(o)
                d["content"]["raw"] = o["raw"]
                return 200, d, {}
        return 404, None, {}


def sajtdata(bas: str) -> dict:
    s = FalskSajt(bas)
    return {f"/wp-json/wp/v2/{t}": [s._offentlig(o) for o in s.objekt[t].values()] for t in ("posts", "pages")} | {
        "/wp-json/wp/v2/categories": s.kategorier}


def hanterare(bas: str, sajt: FalskSajt | None = None):
    sajt = sajt or FalskSajt(bas)

    def hantera(request: httpx.Request) -> httpx.Response:
        kod, data, rubriker = sajt.svar(request.method, request.url.path, dict(request.url.params), request.content)
        return httpx.Response(kod, json=data, headers=rubriker) if data is not None else httpx.Response(kod, headers=rubriker)
    return hantera


def transport(bas: str, sajt: FalskSajt | None = None) -> httpx.MockTransport:
    return httpx.MockTransport(hanterare(bas, sajt))


def asgi_app(bas: str):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    sajt = FalskSajt(bas)

    async def hantera(request):
        body = await request.body()
        kod, data, rubriker = sajt.svar(request.method, request.url.path, dict(request.query_params), body)
        return JSONResponse(data, status_code=kod, headers=rubriker) if data is not None else Response(status_code=kod, headers=rubriker)

    return Starlette(routes=[Route("/{path:path}", hantera, methods=["GET", "HEAD", "POST"])])
