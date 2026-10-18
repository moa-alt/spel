"""WordPress REST API-klient.

Autentisering med användarnamn och applikationslösenord (Basic över HTTPS). Lösenordet
loggas aldrig och skickas aldrig till webbläsaren.
"""

import logging

import httpx

from . import config
from .render import wordpress_block

log = logging.getLogger("studio.wordpress")

# Metafält per SEO-plugin. Fälten måste vara registrerade för REST (tilläggspluginet gör det).
SEO_FALT = {
    "yoast": ("_yoast_wpseo_title", "_yoast_wpseo_metadesc"),
    "rankmath": ("rank_math_title", "rank_math_description"),
}
FOKUS_FALT = {"yoast": "_yoast_wpseo_focuskw", "rankmath": "rank_math_focus_keyword"}
SEO_NAMESPACES = {"yoast/v1": "yoast", "rankmath/v1": "rankmath", "aioseo/v1": "aioseo", "seopress/v1": "seopress"}
STUDIO_NAMESPACE = "klimathemmet-studio/v1"
STUDIO_META = "_kh_studio_id"
ALLA_STATUSAR = "publish,future,draft,pending,private"


class WordPressFel(Exception):
    """Fel med ett meddelande som kan visas för användaren.

    tillfalligt: felet kan gå över av sig självt (timeout, serverfel, för många anrop) och får försökas igen.
    """

    def __init__(self, meddelande: str, tillfalligt: bool = False):
        super().__init__(meddelande)
        self.tillfalligt = tillfalligt


class WordPress:
    def __init__(self, transport: httpx.AsyncBaseTransport | None = None):
        s = config.settings
        if not (s.wp_url and s.wp_user and s.wp_app_password):
            raise WordPressFel("WordPress är inte konfigurerat (WP_URL, WP_USER och WP_APP_PASSWORD).")
        self.bas = s.wp_url.rstrip("/")
        self._klient = httpx.AsyncClient(
            base_url=f"{self.bas}/wp-json",
            auth=(s.wp_user, s.wp_app_password.replace(" ", "")),
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": "Klimathemmet-AI-Studio/1.0"},
            transport=transport,
            follow_redirects=False,
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._klient.aclose()

    async def _anrop(self, metod: str, path: str, tillat: tuple[int, ...] = (), **kw) -> httpx.Response:
        try:
            r = await self._klient.request(metod, path, **kw)
        except httpx.TimeoutException:
            raise WordPressFel("WordPress svarade inte i tid.", tillfalligt=True)
        except httpx.HTTPError:
            raise WordPressFel("Kunde inte ansluta till WordPress.", tillfalligt=True)
        if r.status_code in tillat:
            return r
        if r.status_code in (401, 403):
            raise WordPressFel("WordPress nekade åtkomst. Kontrollera användarnamn och applikationslösenord.")
        if r.status_code >= 400:
            try:
                kod = r.json().get("code", "")
            except ValueError:
                kod = ""
            log.warning("WordPress-fel %s %s: %s %s", metod, path, r.status_code, kod)
            raise WordPressFel(f"WordPress svarade med fel {r.status_code}{f' ({kod})' if kod else ''}.",
                               tillfalligt=r.status_code >= 500 or r.status_code in (408, 429))
        return r

    # ---------- Kontroll ----------

    async def namespaces(self) -> list[str]:
        return (await self._anrop("GET", "/")).json().get("namespaces", [])

    async def kontrollera(self) -> dict:
        """Undersöker sajten utan att ändra något."""
        ns = await self.namespaces()
        jag = (await self._anrop("GET", "/wp/v2/users/me", params={"context": "edit"})).json()
        schema = (await self._anrop("OPTIONS", "/wp/v2/posts")).json()
        meta = sorted(schema.get("schema", {}).get("properties", {}).get("meta", {}).get("properties", {}).keys())
        seo = [namn for n, namn in SEO_NAMESPACES.items() if n in ns]
        caps = jag.get("capabilities", {})
        problem = []
        if not caps.get("publish_posts"):
            problem.append("Användaren saknar behörighet att publicera inlägg.")
        if STUDIO_NAMESPACE not in ns:
            problem.append("Tilläggspluginet Klimathemmet AI Studio är inte installerat. Utan det kan artikelns CSS och SEO-fälten inte skickas säkert.")
        for plugin in seo:
            if plugin in SEO_FALT and not all(f in meta for f in SEO_FALT[plugin]):
                problem.append(f"SEO-fälten för {plugin} är inte tillgängliga via REST API. Installera tilläggspluginet.")
            elif plugin not in SEO_FALT:
                problem.append(f"SEO-pluginet {plugin} stöds inte ännu; metatitel och metabeskrivning skickas inte.")
        if not seo:
            problem.append("Hittade inget SEO-plugin (Yoast eller Rank Math).")
        return {
            "anvandare": jag.get("name"),
            "kan_publicera": bool(caps.get("publish_posts")),
            "seo_plugin": seo,
            "studio_plugin": STUDIO_NAMESPACE in ns,
            "meta_falt": meta,
            "problem": problem,
        }

    # ---------- Inlägg ----------

    async def hitta_inlagg(self, slug: str) -> list[dict]:
        r = await self._anrop("GET", "/wp/v2/posts", params={"slug": slug, "status": ALLA_STATUSAR, "context": "edit"})
        return r.json()

    async def hamta_inlagg(self, post_id: int) -> dict:
        return (await self._anrop("GET", f"/wp/v2/posts/{post_id}", params={"context": "edit"})).json()

    async def spara_inlagg(self, artikel: dict, status: str, datum_gmt: str | None = None) -> dict:
        """Skapar eller uppdaterar artikelns inlägg. Publicerar aldrig samma artikel två gånger.

        Ett befintligt inlägg återanvänds om det är markerat med artikelns studio-id eller har
        artikelns sparade wp_post_id. Ett annat inlägg med samma adress stoppar publiceringen.
        """
        innehall = artikel["innehall"]
        studio_id = f"artikel-{artikel['id']}"
        ns = await self.namespaces()
        plugin = STUDIO_NAMESPACE in ns
        schema = (await self._anrop("OPTIONS", "/wp/v2/posts")).json()
        tillatna_meta = set(schema.get("schema", {}).get("properties", {}).get("meta", {}).get("properties", {}))

        befintlig = None
        for post in await self.hitta_inlagg(innehall["slug"]):
            var_egen = post.get("meta", {}).get(STUDIO_META) == studio_id or post.get("id") == artikel.get("wp_post_id")
            if not var_egen:
                raise WordPressFel(f"Det finns redan ett annat inlägg med adressen /{innehall['slug']}/ på sajten.")
            befintlig = post
        if befintlig is None and artikel.get("wp_post_id"):
            try:
                befintlig = await self.hamta_inlagg(artikel["wp_post_id"])
            except WordPressFel:
                befintlig = None
        if befintlig and befintlig.get("status") == "publish":
            # Redan publicerat, till exempel om servern startades om mitt i en publicering.
            return befintlig

        # Bilderna laddas upp till mediabiblioteket först nu, när artikeln faktiskt skickas.
        from .bilder import ladda_upp_till_wordpress

        media = await ladda_upp_till_wordpress(self, artikel)

        meta = {}
        if STUDIO_META in tillatna_meta:
            meta[STUDIO_META] = studio_id
        for plugin_namn, (titel_falt, beskr_falt) in SEO_FALT.items():
            if f"{plugin_namn}/v1" in ns and titel_falt in tillatna_meta and beskr_falt in tillatna_meta:
                meta[titel_falt] = innehall["meta_titel"]
                meta[beskr_falt] = innehall["meta_beskrivning"]
                if FOKUS_FALT[plugin_namn] in tillatna_meta:
                    meta[FOKUS_FALT[plugin_namn]] = artikel["huvudsokord"]
        # Länkar till godkända framtida mål som ännu inte publicerats blir vanlig text, så att inga trasiga länkar publiceras.
        from .kvalitet import ej_publicerade_mal

        utan_lankar = ej_publicerade_mal(artikel)

        data = {
            "title": innehall["titel"],
            "slug": innehall["slug"],
            # Med tilläggspluginet laddas CSS:en som en fil; annars bäddas den in i blocket.
            "content": wordpress_block(innehall, inline_css=not plugin, media=media, utan_lankar=utan_lankar),
            "excerpt": innehall["meta_beskrivning"],
            "status": status,
        }
        if meta:
            data["meta"] = meta
        if 1 in media:
            # Huvudbilden blir också inläggets utvalda bild.
            data["featured_media"] = media[1]["id"]
        if innehall.get("kategori_id") is not None:
            data["categories"] = [int(innehall["kategori_id"])]
        if datum_gmt:
            # Publiceringsdatum i UTC enligt kalendern (WordPress visar det i sajtens tidszon).
            data["date_gmt"] = datum_gmt
        if befintlig:
            post = (await self._anrop("POST", f"/wp/v2/posts/{befintlig['id']}", json=data)).json()
        else:
            post = (await self._anrop("POST", "/wp/v2/posts", json=data)).json()

        # Verifiera att WordPress faktiskt har inlägget med rätt status.
        kontroll = await self.hamta_inlagg(post["id"])
        if kontroll.get("status") != status:
            raise WordPressFel(f"WordPress sparade inlägget med status {kontroll.get('status')} i stället för {status}.")
        return kontroll

    # ---------- Mediabiblioteket ----------

    async def ladda_upp_media(self, data: bytes, filnamn: str, alt: str, titel: str) -> dict:
        r = await self._anrop("POST", "/wp/v2/media", content=data, headers={
            "Content-Type": "image/webp", "Content-Disposition": f'attachment; filename="{filnamn}"'})
        media = r.json()
        return (await self._anrop("POST", f"/wp/v2/media/{media['id']}", json={"alt_text": alt, "title": titel})).json()

    async def hamta_media(self, media_id: int) -> dict | None:
        """Bilden i mediabiblioteket, eller None om den har tagits bort där."""
        r = await self._anrop("GET", f"/wp/v2/media/{media_id}", tillat=(404, 410), params={"context": "edit"})
        return None if r.status_code in (404, 410) else r.json()

    async def uppdatera_media(self, media_id: int, data: dict) -> dict:
        return (await self._anrop("POST", f"/wp/v2/media/{media_id}", json=data)).json()

    # ---------- Befintliga inlägg och sidor ----------

    @staticmethod
    def _typvag(typ: str) -> str:
        return {"post": "posts", "page": "pages"}[typ]

    async def hamta_redigerbar(self, typ: str, wp_id: int) -> dict:
        """Innehållet som det är sparat i WordPress (raw), med ändringstid. Ändrar ingenting."""
        r = await self._anrop("GET", f"/wp/v2/{self._typvag(typ)}/{wp_id}", params={"context": "edit"})
        return r.json()

    async def uppdatera_innehall(self, typ: str, wp_id: int, raw: str) -> dict:
        """Sparar nytt innehåll. Anropas bara efter godkännande och konfliktkontroll."""
        return (await self._anrop("POST", f"/wp/v2/{self._typvag(typ)}/{wp_id}", json={"content": raw})).json()
