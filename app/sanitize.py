"""Tillåtelselista för HTML från AI:n.

Allt som inte uttryckligen är tillåtet tas bort (skript, stilar, iframes,
on*-attribut, javascript:-länkar). Bildmarkörerna <!--BILD:n--> behålls.
"""

import html
import re
from html.parser import HTMLParser

TILLATNA_TAGGAR = {
    "p", "h2", "h3", "h4", "ul", "ol", "li", "strong", "em", "b", "i", "a", "br",
    "div", "span", "nav", "table", "thead", "tbody", "tr", "th", "td",
    "details", "summary", "blockquote",
}
TOMMA_TAGGAR = {"br"}
TILLATNA_ATTR = {"class", "id", "href", "aria-label", "colspan", "rowspan", "scope", "title"}
# Innehållet i de här taggarna kastas helt, inte bara själva taggen.
KASTA_INNEHALL = {"script", "style", "iframe", "object", "embed", "svg", "math", "template", "noscript"}
BILD_MARKOR = re.compile(r"^BILD:(\d+)$")


def _saker_href(url: str) -> bool:
    url = url.strip().lower()
    return url.startswith(("https://", "http://", "#", "/")) and not url.startswith("//")


class _Rensare(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.ut: list[str] = []
        self.oppna: list[str] = []
        self.kasta_djup = 0

    def handle_starttag(self, tag, attrs):
        if tag in KASTA_INNEHALL:
            self.kasta_djup += 1
            return
        if self.kasta_djup or tag not in TILLATNA_TAGGAR:
            return
        delar = [tag]
        for namn, varde in attrs:
            if namn not in TILLATNA_ATTR or varde is None:
                continue
            if namn == "href" and not _saker_href(varde):
                continue
            delar.append(f'{namn}="{html.escape(varde, quote=True)}"')
        self.ut.append("<" + " ".join(delar) + ">")
        if tag not in TOMMA_TAGGAR:
            self.oppna.append(tag)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in self.oppna and tag not in TOMMA_TAGGAR:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in KASTA_INNEHALL:
            self.kasta_djup = max(0, self.kasta_djup - 1)
            return
        if self.kasta_djup or tag not in self.oppna:
            return
        # Stäng även taggar som AI:n glömt stänga innanför.
        while self.oppna:
            senaste = self.oppna.pop()
            self.ut.append(f"</{senaste}>")
            if senaste == tag:
                break

    def handle_data(self, data):
        if not self.kasta_djup:
            self.ut.append(html.escape(data, quote=False))

    def handle_comment(self, data):
        m = BILD_MARKOR.match(data.strip())
        if m and not self.kasta_djup:
            self.ut.append(f"<!--BILD:{m.group(1)}-->")


def rensa_html(raw: str) -> str:
    p = _Rensare()
    p.feed(raw)
    p.close()
    p.ut.extend(f"</{t}>" for t in reversed(p.oppna))
    return "".join(p.ut).strip()
