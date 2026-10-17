"""En liten låtsas-WordPress med artiklar, sidor, kategorier och länkar, för tester."""

import httpx


def sajtdata(bas: str) -> dict:
    def post(pid, slug, titel, html, kategori=None, sida=False):
        d = {"id": pid, "link": f"{bas}/{slug}/", "slug": slug, "title": {"rendered": titel},
             "content": {"rendered": html}, "modified": "2026-09-01T10:00:00",
             "yoast_head_json": {"title": titel, "description": "" if pid == 3 else f"Om {titel.lower()}"}}
        if not sida:
            d["categories"] = [kategori]
        return d

    def a(slug, text):
        return f'<a href="{bas}/{slug}/">{text}</a>'

    return {
        "/wp-json/wp/v2/posts": [
            post(1, "ventilation-i-gamla-hus", "Ventilation i gamla hus",
                 f"<h2>Självdrag</h2><p>Läs om {a('fukt-i-kallare', 'fukt i källaren')} och {a('ftx-ventilation', 'FTX')}.</p>"
                 f"<p>{a('borttagen-sida', 'en gammal guide')} och {a('kategori/ventilation', 'allt om ventilation')}</p>", 10),
            post(2, "ftx-ventilation", "FTX-ventilation i villa", f"<p>Se {a('ventilation-i-gamla-hus', 'ventilation i äldre hus')}.</p>", 10),
            post(3, "fukt-i-kallare", "Fukt i källare", f"<p>Mer om {a('ventilation-i-gamla-hus', 'ventilation')}.</p>", 11),
            post(4, "ventilation-gamla-hus-tips", "Ventilation i gamla hus – tips", "<p>Tips utan länkar.</p>", 10),
            post(5, "fran-ventilation-till-ftx", "Från självdrag till FTX-ventilation", f"<p>{a('ventilation-i-gamla-hus', 'pelaren')}</p>", 10),
        ],
        "/wp-json/wp/v2/pages": [post(20, "offert-ventilation", "Offert på ventilation", f"<p>{a('ftx-ventilation', 'FTX')}</p>", sida=True)],
        "/wp-json/wp/v2/categories": [
            {"id": 10, "link": f"{bas}/kategori/ventilation/", "name": "Ventilation", "slug": "ventilation", "count": 4, "description": ""},
            {"id": 11, "link": f"{bas}/kategori/fukt/", "name": "Fukt", "slug": "fukt", "count": 1, "description": ""},
        ],
    }


def svar(bas: str, metod: str, path: str, sida: str | None) -> tuple[int, object, dict]:
    if metod == "HEAD":
        return (404 if "borttagen" in path else 200), None, {}
    data = sajtdata(bas).get(path)
    if data is None:
        return 404, None, {}
    if sida != "1":
        return 400, {"code": "rest_post_invalid_page_number"}, {}
    return 200, data, {"X-WP-TotalPages": "1"}


def transport(bas: str) -> httpx.MockTransport:
    def hantera(request: httpx.Request) -> httpx.Response:
        kod, data, rubriker = svar(bas, request.method, request.url.path, request.url.params.get("page"))
        return httpx.Response(kod, json=data, headers=rubriker) if data is not None else httpx.Response(kod, headers=rubriker)
    return hantera


def asgi_app(bas: str):
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, Response
    from starlette.routing import Route

    async def hantera(request):
        kod, data, rubriker = svar(bas, request.method, request.url.path, request.query_params.get("page"))
        return JSONResponse(data, status_code=kod, headers=rubriker) if data is not None else Response(status_code=kod, headers=rubriker)

    return Starlette(routes=[Route("/{path:path}", hantera, methods=["GET", "HEAD"])])
