"""Gör om artikeln till den kod som läggs i WordPress (ett Anpassad HTML-block)."""

import html
import re
from pathlib import Path

ARTIKEL_CSS = Path(__file__).resolve().parent / "static" / "artikel.css"
BILD_MARKOR = re.compile(r"<!--BILD:(\d+)-->")


# Artikelns textbredd är 760 px; på mindre skärmar fyller bilden hela bredden.
SIZES = "(max-width: 800px) 100vw, 760px"


def figure_html(bild: dict, media: dict | None = None) -> str:
    """En bild från WordPress mediabibliotek, med srcset, width/height och lazy loading.

    Bilder som ännu inte laddats upp till WordPress tas inte med; studions egna adresser når inte besökarna.
    """
    if not media or not media.get("url"):
        return ""
    e = lambda v: html.escape(str(v), quote=True)  # noqa: E731
    attr = [f'class="wp-image-{int(media["id"])}"', f'src="{e(media["url"])}"']
    kandidater = list(media.get("storlekar") or [])
    if media.get("bredd") and all(k["bredd"] != media["bredd"] for k in kandidater):
        kandidater.append({"url": media["url"], "bredd": media["bredd"]})
    if len(kandidater) > 1:
        srcset = ", ".join(f'{e(k["url"])} {int(k["bredd"])}w' for k in sorted(kandidater, key=lambda k: k["bredd"]))
        attr += [f'srcset="{srcset}"', f'sizes="{SIZES}"']
    if media.get("bredd") and media.get("hojd"):
        attr += [f'width="{int(media["bredd"])}"', f'height="{int(media["hojd"])}"']
    attr.append(f'alt="{e(bild.get("alt", ""))}"')
    # Huvudbilden kan synas direkt och laddas därför genast; övriga bilder laddas när besökaren närmar sig dem.
    attr.append('fetchpriority="high" decoding="async"' if bild.get("id") == 1 else 'loading="lazy" decoding="async"')
    fig = f'<figure class="kh-bild"><img {" ".join(attr)}>'
    caption = bild.get("bildtext", "").strip()
    if caption:
        fig += f"<figcaption>{html.escape(caption)}</figcaption>"
    return fig + "</figure>"


def article_inner_html(article: dict, media: dict[int, dict] | None = None) -> str:
    bilder = {b["id"]: b for b in article.get("bilder", [])}
    media = media or {}
    return BILD_MARKOR.sub(
        lambda m: figure_html(bilder.get(int(m.group(1)), {}), media.get(int(m.group(1)))), article["html"])


def wordpress_block(article: dict, inline_css: bool = True, media: dict[int, dict] | None = None) -> str:
    """Artikeln som ett Anpassad HTML-block. Utan inline_css förutsätts tilläggspluginet ladda CSS:en."""
    stil = f"<style>\n{ARTIKEL_CSS.read_text(encoding='utf-8').strip()}\n</style>\n" if inline_css else ""
    return (
        "<!-- wp:html -->\n"
        f"{stil}"
        f'<div class="kh-artikel">\n{article_inner_html(article, media)}\n</div>\n'
        "<!-- /wp:html -->"
    )
