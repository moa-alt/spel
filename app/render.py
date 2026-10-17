"""Gör om artikeln till den kod som läggs i WordPress (ett Anpassad HTML-block)."""

import html
import re
from pathlib import Path

ARTIKEL_CSS = Path(__file__).resolve().parent / "static" / "artikel.css"
BILD_MARKOR = re.compile(r"<!--BILD:(\d+)-->")


def figure_html(bild: dict) -> str:
    if not bild.get("url"):
        return ""
    alt = html.escape(bild.get("alt", ""), quote=True)
    src = html.escape(bild["url"], quote=True)
    caption = bild.get("bildtext", "").strip()
    fig = f'<figure class="kh-bild"><img src="{src}" alt="{alt}" loading="lazy" decoding="async">'
    if caption:
        fig += f"<figcaption>{html.escape(caption)}</figcaption>"
    return fig + "</figure>"


def article_inner_html(article: dict) -> str:
    bilder = {b["id"]: b for b in article.get("bilder", [])}
    return BILD_MARKOR.sub(lambda m: figure_html(bilder.get(int(m.group(1)), {})), article["html"])


def wordpress_block(article: dict) -> str:
    css = ARTIKEL_CSS.read_text(encoding="utf-8").strip()
    return (
        "<!-- wp:html -->\n"
        f"<style>\n{css}\n</style>\n"
        f'<div class="kh-artikel">\n{article_inner_html(article)}\n</div>\n'
        "<!-- /wp:html -->"
    )
