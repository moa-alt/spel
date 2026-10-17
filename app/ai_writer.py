"""AI-generering av artiklar med Claude.

generera_artikel() är en asynkron generator som först skickar förloppshändelser
och till sist den färdiga artikeln, så att gränssnittet kan visa vad som händer.
"""

import json
import re
from collections.abc import AsyncIterator

import anthropic

from .config import settings
from .demo import slugify
from .sanitize import rensa_html

SYSTEMPROMPT = """Du är seniorskribent och SEO-specialist på Klimathemmet (klimathemmet.se), en svensk sajt om inomhusklimat, ventilation, uppvärmning, värmepumpar, isolering, fukt och energibesparing i villor och äldre hus.

Du skriver kompletta, sökordsoptimerade guideartiklar på svenska som besvarar sökintentionen bättre än konkurrenterna.

# Arbetsgång
1. Analysera sökintentionen bakom huvudsökordet: vad vill personen som söker veta eller göra, och vilken typ av sida rankar för sökningen (guide, jämförelse, kostnadsartikel, felsökning)?
2. Planera en struktur som täcker frågorna som läsaren har, i den ordning hen har dem. Besvara huvudfrågan tidigt.
3. Skriv artikeln.

# Språk och ton
- Klar, saklig och hjälpsam svenska. Tilltala läsaren med "du". Korta stycken, 2–4 meningar.
- Konkreta råd, exempel och siffror där det hjälper läsaren. Inget fluff, inga utfyllnadsfraser och inga klyschor som "i dagens samhälle".
- Hitta inte på statistik, studier, citat, produktnamn eller exakta regler. Ange kostnader som ungefärliga spann ("ungefär", "ofta") och skriv aldrig att en siffra gäller ett visst år. Nämn ROT-avdrag, bygglov eller regler allmänt och hänvisa till Skatteverket, Boverket eller kommunen för aktuella villkor.
- Rekommendera fackman när något rör el, fukt i konstruktion, eldstäder eller bärande delar.

# SEO
- Huvudsökordet ska stå i titeln, metatiteln, metabeskrivningen, slugen, första stycket och minst en H2. Använd det naturligt, utan att överanvända det.
- Väv in varje sekundärt sökord naturligt i löptext eller underrubriker, gärna i exakt form minst en gång.
- Täck närliggande frågor som folk också söker på. Ta med dem i avsnittet med vanliga frågor.
- titel (H1): tydlig och lockande, högst cirka 65 tecken.
- meta_titel: 50–60 tecken, börjar gärna med huvudsökordet. Skriv inte ut sajtnamnet.
- meta_beskrivning: 130–155 tecken, sammanfattar nyttan och slutar med en uppmaning.
- slug: huvudsökordet med små bokstäver och bindestreck, utan å, ä och ö (å och ä blir a, ö blir o).
- Längd: 1 400–2 200 ord brödtext, beroende på hur mycket ämnet kräver.

# HTML-format (viktigt)
Fältet html innehåller bara artikelns brödtext. Den ska INTE innehålla H1, <html>, <body>, <style>, <script>, inline-stil, bilder eller markdown. Använd endast taggarna p, h2, h3, ul, ol, li, strong, em, a, table, thead, tbody, tr, th, td, div, nav, details och summary, samt designens klasser nedan. Varje h2 ska ha ett id i slug-format som innehållsförteckningen länkar till.

Artikeln följer Klimathemmets design med dessa komponenter, i ungefär den här ordningen:

<p class="kh-ingress">Ingress på 2–3 meningar som innehåller huvudsökordet och direkt besvarar vad läsaren vill veta.</p>

<div class="kh-snabbfakta"><p class="kh-box-rubrik">Det viktigaste i korthet</p><ul><li>3–5 konkreta punkter</li></ul></div>

<nav class="kh-toc" aria-label="Innehåll"><p class="kh-box-rubrik">Innehåll</p><ol><li><a href="#h2-id">H2-rubrik</a></li>…</ol></nav>

Därefter 5–8 avsnitt med <h2 id="...">, gärna med <h3>, stycken och listor. Använd där de passar innehållet (inte alla måste vara med, och ingen ruta får vara utfyllnad):
- Tipsruta: <div class="kh-tips"><p class="kh-box-rubrik">Tips</p><p>…</p></div>
- Varningsruta: <div class="kh-varning"><p class="kh-box-rubrik">Tänk på</p><p>…</p></div>
- För- och nackdelar: <div class="kh-procon"><div class="kh-pro"><p class="kh-box-rubrik">Fördelar</p><ul>…</ul></div><div class="kh-con"><p class="kh-box-rubrik">Nackdelar</p><ul>…</ul></div></div>
- Tabell, till exempel för kostnader eller jämförelser: <div class="kh-tabell-wrap"><table class="kh-tabell"><thead><tr><th>…</th></tr></thead><tbody><tr><td>…</td></tr></tbody></table></div>
- Numrerade steg: <ol class="kh-steg"><li><strong>Steg.</strong> Förklaring.</li></ol>

Avsnittet med vanliga frågor är en h2 följd av:
<div class="kh-faq"><details><summary>Fråga?</summary><p>Svar på 2–4 meningar.</p></details>…</div> med 4–6 frågor.

Avsluta med:
<div class="kh-sammanfattning"><p class="kh-box-rubrik">Sammanfattning</p><p>…</p></div>

# Bilder
Artikeln har exakt fyra bilder. Placera markörerna <!--BILD:1-->, <!--BILD:2-->, <!--BILD:3--> och <!--BILD:4--> var för sig på egen rad i html, utspridda i artikeln: BILD:1 efter innehållsförteckningen, BILD:2 och BILD:3 i början av två olika avsnitt i mitten och BILD:4 före sammanfattningen.
För varje bild i fältet bilder, i samma ordning:
- alt: beskriver vad som syns på bilden, på svenska, 8–16 ord. Ta med huvud- eller sekundärsökord där det är naturligt.
- bildtext: kort bildtext på svenska, eller tom sträng.
- prompt: detaljerad beskrivning på engelska för en bildgenerator. Fotorealistisk redaktionell bild i skandinavisk miljö med naturligt ljus, utan text, logotyper eller vattenstämplar, och utan igenkännbara ansikten i närbild. Bilden ska visa något konkret och relevant för just det avsnittet.
"""

ARTIKEL_SCHEMA = {
    "type": "object",
    "properties": {
        "sokintention": {
            "type": "object",
            "properties": {
                "typ": {"type": "string", "enum": ["informativ", "kommersiell", "transaktionell", "navigerande"]},
                "beskrivning": {"type": "string"},
                "malgrupp": {"type": "string"},
            },
            "required": ["typ", "beskrivning", "malgrupp"],
            "additionalProperties": False,
        },
        "titel": {"type": "string"},
        "slug": {"type": "string"},
        "meta_titel": {"type": "string"},
        "meta_beskrivning": {"type": "string"},
        "html": {"type": "string"},
        "bilder": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "alt": {"type": "string"},
                    "bildtext": {"type": "string"},
                    "prompt": {"type": "string"},
                },
                "required": ["alt", "bildtext", "prompt"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["sokintention", "titel", "slug", "meta_titel", "meta_beskrivning", "html", "bilder"],
    "additionalProperties": False,
}

ANTAL_BILDER = 4


class GenereringsFel(Exception):
    """Fel med ett meddelande som kan visas för användaren."""


def _anvandarprompt(huvudsokord: str, sekundara: list[str]) -> str:
    sek = "\n".join(f"- {s}" for s in sekundara) or "- (inga angivna, välj själv relevanta närliggande termer)"
    return f"Skriv en komplett artikel.\n\nHuvudsökord: {huvudsokord}\n\nSekundära sökord:\n{sek}"


def _placera_bildmarkorer(body: str) -> str:
    """Ser till att varje bildmarkör finns exakt en gång, även om AI:n missat någon."""
    for n in range(1, ANTAL_BILDER + 1):
        markor = f"<!--BILD:{n}-->"
        forsta = body.find(markor)
        if forsta != -1:
            body = body[: forsta + len(markor)] + body[forsta + len(markor):].replace(markor, "")
    saknas = [n for n in range(1, ANTAL_BILDER + 1) if f"<!--BILD:{n}-->" not in body]
    if not saknas:
        return body
    h2_pos = [m.start() for m in re.finditer(r"<h2[\s>]", body)]
    for n in reversed(saknas):
        # Fördela saknade bilder före de första H2-avsnitten, annars sist i texten.
        pos = h2_pos[min(n - 1, len(h2_pos) - 1)] if h2_pos else len(body)
        body = body[:pos] + f"<!--BILD:{n}-->\n" + body[pos:]
    return body


def _artikel_fran_json(data: dict, huvudsokord: str, sekundara: list[str]) -> dict:
    bilder = list(data.get("bilder") or [])[:ANTAL_BILDER]
    while len(bilder) < ANTAL_BILDER:
        bilder.append({"alt": huvudsokord, "bildtext": "", "prompt": f"Editorial photo about {huvudsokord}, Scandinavian home"})
    return {
        "titel": data["titel"].strip(),
        "slug": slugify(data.get("slug") or huvudsokord) or slugify(huvudsokord),
        "meta_titel": data["meta_titel"].strip(),
        "meta_beskrivning": data["meta_beskrivning"].strip(),
        "html": _placera_bildmarkorer(rensa_html(data["html"])),
        "bilder": [
            {"id": i + 1, "alt": b["alt"].strip(), "bildtext": b["bildtext"].strip(), "prompt": b["prompt"].strip(), "url": None}
            for i, b in enumerate(bilder)
        ],
        "sokintention": data.get("sokintention"),
        "huvudsokord": huvudsokord,
        "sekundara_sokord": sekundara,
        "demo": False,
    }


def _slutlig_text(message) -> str:
    """Texten i svaret. Om en reservmodell tagit över efter ett avbrott används bara dess del."""
    block = list(message.content)
    sista_reserv = max((i for i, b in enumerate(block) if b.type == "fallback"), default=-1)
    return "".join(b.text for b in block[sista_reserv + 1:] if b.type == "text")


async def generera_artikel(huvudsokord: str, sekundara: list[str]) -> AsyncIterator[dict]:
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key, max_retries=3)
    yield {"typ": "steg", "steg": 0}
    skriver = False
    tecken = 0
    try:
        async with client.messages.stream(
            model=settings.claude_model,
            max_tokens=64000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": settings.claude_effort,
                "format": {"type": "json_schema", "schema": ARTIKEL_SCHEMA},
            },
            system=[{"type": "text", "text": SYSTEMPROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _anvandarprompt(huvudsokord, sekundara)}],
            # Om modellen avböjer tar en reservmodell över automatiskt.
            extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"},
            extra_body={"fallbacks": "default"},
        ) as stream:
            async for event in stream:
                if event.type == "content_block_start" and event.content_block.type == "text" and not skriver:
                    skriver = True
                    yield {"typ": "steg", "steg": 1}
                elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                    tecken += len(event.delta.text)
                    if tecken // 2000 != (tecken - len(event.delta.text)) // 2000:
                        yield {"typ": "framsteg", "tecken": tecken}
            message = await stream.get_final_message()
    except anthropic.AuthenticationError:
        raise GenereringsFel("API-nyckeln godkändes inte. Kontrollera ANTHROPIC_API_KEY i .env och starta om servern.")
    except anthropic.PermissionDeniedError:
        raise GenereringsFel("API-nyckeln saknar behörighet till modellen. Kontrollera nyckeln i Anthropic Console.")
    except anthropic.RateLimitError:
        raise GenereringsFel("För många anrop just nu. Vänta en stund och försök igen.")
    except anthropic.APIStatusError as e:
        if e.status_code == 402 or getattr(e, "type", "") == "billing_error":
            raise GenereringsFel("Anthropic-kontot saknar krediter. Fyll på under Billing i Anthropic Console.")
        if e.status_code >= 500 or e.status_code == 529:
            raise GenereringsFel("Claude är tillfälligt överbelastad. Försök igen om en stund.")
        raise GenereringsFel(f"Anropet till Claude misslyckades ({e.status_code}).")
    except anthropic.APIConnectionError:
        raise GenereringsFel("Kunde inte nå Claude. Kontrollera serverns internetanslutning.")

    if message.stop_reason == "refusal":
        raise GenereringsFel("Claude avböjde att skriva om det här ämnet. Prova att formulera sökordet annorlunda.")
    if message.stop_reason == "max_tokens":
        raise GenereringsFel("Svaret blev för långt och klipptes av. Försök igen.")

    yield {"typ": "steg", "steg": 3}
    try:
        data = json.loads(_slutlig_text(message))
        artikel = _artikel_fran_json(data, huvudsokord, sekundara)
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError):
        raise GenereringsFel("Svaret från Claude kunde inte tolkas. Försök igen.")
    yield {"typ": "klar", "artikel": artikel}
