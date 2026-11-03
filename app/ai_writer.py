"""AI-generering av artiklar med Claude.

generera_artikel() är en asynkron generator som först skickar förloppshändelser
och till sist den färdiga artikeln, så att gränssnittet kan visa vad som händer.
"""

import json
from collections.abc import AsyncIterator

import anthropic

from . import config
from .demo import slugify
from . import design

SYSTEMPROMPT = """Du är seniorskribent och SEO-specialist på Klimathemmet (klimathemmet.se), en svensk sajt om inomhusklimat, ventilation, uppvärmning, värmepumpar, isolering, fukt och energibesparing i villor och äldre hus.

Du skriver kompletta, sökordsoptimerade guideartiklar på svenska som besvarar sökintentionen bättre än konkurrenterna.

# Arbetsgång
1. Analysera sökintentionen bakom huvudsökordet: vad vill personen som söker veta eller göra, och vilken typ av sida rankar för sökningen (guide, jämförelse, kostnadsartikel, felsökning)?
2. Planera en struktur som täcker frågorna som läsaren har, i den ordning hen har dem. Besvara huvudfrågan tidigt.
3. Skriv artikeln.

# Språk och ton
- Klar, saklig och hjälpsam svenska. Tilltala läsaren med "du". Korta stycken, 2–4 meningar.
- Konkreta råd, exempel och siffror där det hjälper läsaren. Inget fluff, inga utfyllnadsfraser och inga klyschor som "i dagens samhälle".
- Hitta aldrig på fakta, statistik, studier, tester, produktrecensioner, citat, källor, källhänvisningar, produktnamn eller exakta regler. Skriv inte att något är testat eller att du har provat något. Är du osäker på en uppgift, utelämna den eller formulera dig allmänt. Ange kostnader som ungefärliga spann ("ungefär", "ofta") och skriv aldrig att en siffra gäller ett visst år. Nämn ROT-avdrag, bygglov eller regler allmänt och hänvisa till Skatteverket, Boverket eller kommunen för aktuella villkor.
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

# Format: strukturerat innehåll (viktigt)
Du skriver innehållet; Klimathemmets design läggs på automatiskt. Skriv därför ingen HTML-layout, inga klasser och ingen CSS. I textfält får du bara använda <strong>, <em> och <a href="URL">ankartext</a>. Rubriker (rubrik, underrubrik, meny, etikett) är ren text.

Artikeln (fältet dokument) består av:
- hero: etikett (kort kategorirad i versaler, t.ex. "VÄRME & ENERGI / GUIDE"), ingress (artikelns första stycke: 2–4 meningar som innehåller huvudsökordet och direkt besvarar vad läsaren vill veta) och gärna knapp (kort uppmaning, t.ex. "Se 7 åtgärder", med mal = id för den sektion den leder till). Titeln (H1) visas i heron.
- intro: etikett, rubrik (H2), 2–4 stycken som sätter in läsaren i ämnet, och fakta: en mörkgrön snabböversikt med etikett, rubrik och 3–5 punkter (titel + kort text).
- sektioner: 5–9 avsnitt i den ordning läsaren behöver dem. Varje sektion har id (slug), layout, etikett (kort, i versaler), rubrik (H2), meny (1–3 ord till innehållsförteckningen), valfri ingress, valfri bild och en lista med block. Minst en rubrik innehåller huvudsökordet.
- sammanfattning: rubrik och 1–3 stycken.
- faq: rubrik och 4–8 vanliga frågor med svar på 2–4 meningar.
- relaterade_rubrik: rubrik för relaterade guider (själva länkarna väljs automatiskt).
Innehållsförteckning och relaterade artiklar skapas automatiskt; skriv dem inte själv.

## Layouter – välj efter innehållet
- standard: vanligt textavsnitt.
- mork: mörkgrön sektion i full bredd med bild bredvid texten. Passar när något förklaras, t.ex. hur ett system fungerar. Högst två per artikel.
- bild: text och bild sida vid sida; sidan växlar automatiskt. Passar avsnitt där en bild gör texten tydligare.
- losningar: gräddvit sektion i full bredd för åtgärder och rekommendationer, oftast med kort av varianten losning.
Låt inte två sektioner i full bredd (mork, losningar) komma direkt efter varandra. Strukturen ska följa just det här ämnet; använd inte samma ordning och samma komponenter i varje artikel.

## Block – välj de som tillför något
- stycke: text på 2–4 meningar.
- underrubrik: H3 inom sektionen.
- lista: punktlista. checklista: punkter med bock för saker att kontrollera, tänka på eller ta med.
- steg: numrerade steg med rubrik och text, för instruktioner som görs i ordning.
- notis: informationsruta med etikett (t.ex. "Viktigt:", "Tips:", "Kom ihåg:", "Rekommendation:") och text.
- kort: 3–6 kort med rubrik och text. variant problem för problem, tecken och orsaker; variant losning för åtgärder och rekommendationer.
- tabell: jämförelse med 2–4 kolumner (kolumner) och 3–8 rader (rader, en lista med celler per rad). Använd bara när läsaren faktiskt jämför alternativ, system eller kostnader.
En artikel om ett enkelt ämne behöver inte både tabell, kort och steg. Ingen ruta eller lista får vara utfyllnad.

# Bilder
Artikeln har exakt fyra bilder. Bild 1 är huvudbilden i heron. Bild 2, 3 och 4 placeras i tre olika sektioner, en var, genom fältet bild i sektionen; de sektionerna ska ha layouten mork eller bild. Välj avsnitt där en bild verkligen hjälper läsaren, inte avsnittet med vanliga frågor.
För varje bild i fältet bilder, i ordningen bild 1–4:
- alt: naturlig svensk mening som beskriver vad som faktiskt syns på bilden, 8–16 ord. Börja inte med "Bild på". Ta med huvud- eller sekundärsökord bara där det är naturligt.
- bildtext: kort bildtext på svenska, eller tom sträng.
- prompt: detaljerad beskrivning på engelska av motivet för en bildgenerator. Välj motivet utifrån artikelns ämne och just det avsnittets innehåll: något konkret som en svensk villaägare känner igen (rätt typ av hus, rum, installation eller arbetsmoment). Huvudbilden visar ämnet i stort. Fotorealistisk, ljus redaktionell bild i skandinavisk miljö med naturligt ljus, utan text, logotyper eller vattenstämplar, och utan igenkännbara ansikten i närbild.
"""

_TEXT = {"type": "string"}


def _objekt(egenskaper: dict, kravda: list[str]) -> dict:
    return {"type": "object", "properties": egenskaper, "required": kravda, "additionalProperties": False}


_BLOCK = _objekt({
    "typ": {"type": "string", "enum": ["stycke", "underrubrik", "lista", "checklista", "steg", "notis", "kort", "tabell"]},
    "text": _TEXT,
    "etikett": _TEXT,
    "variant": {"type": "string", "enum": ["problem", "losning"]},
    "punkter": {"type": "array", "items": _objekt({"rubrik": _TEXT, "text": _TEXT}, ["text"])},
    "kolumner": {"type": "array", "items": _TEXT},
    "rader": {"type": "array", "items": {"type": "array", "items": _TEXT}},
}, ["typ"])

_SEKTION = _objekt({
    "id": _TEXT,
    "layout": {"type": "string", "enum": ["standard", "mork", "bild", "losningar"]},
    "etikett": _TEXT,
    "rubrik": _TEXT,
    "meny": _TEXT,
    "ingress": _TEXT,
    "bild": {"type": "integer", "enum": [2, 3, 4]},
    "block": {"type": "array", "items": _BLOCK},
}, ["id", "layout", "etikett", "rubrik", "meny", "block"])

_DOKUMENT = _objekt({
    "hero": _objekt({"etikett": _TEXT, "ingress": _TEXT, "knapp": _objekt({"text": _TEXT, "mal": _TEXT}, ["text", "mal"])},
                    ["etikett", "ingress"]),
    "intro": _objekt({
        "etikett": _TEXT, "rubrik": _TEXT, "stycken": {"type": "array", "items": _TEXT},
        "fakta": _objekt({"etikett": _TEXT, "rubrik": _TEXT,
                          "punkter": {"type": "array", "items": _objekt({"titel": _TEXT, "text": _TEXT}, ["titel", "text"])}},
                         ["etikett", "rubrik", "punkter"]),
    }, ["etikett", "rubrik", "stycken", "fakta"]),
    "sektioner": {"type": "array", "items": _SEKTION},
    "sammanfattning": _objekt({"rubrik": _TEXT, "stycken": {"type": "array", "items": _TEXT}}, ["rubrik", "stycken"]),
    "faq": _objekt({"rubrik": _TEXT, "fragor": {"type": "array", "items": _objekt({"fraga": _TEXT, "svar": _TEXT}, ["fraga", "svar"])}},
                   ["rubrik", "fragor"]),
    "relaterade_rubrik": _TEXT,
}, ["hero", "intro", "sektioner", "sammanfattning", "faq", "relaterade_rubrik"])

ARTIKEL_SCHEMA = _objekt({
    "sokintention": _objekt({
        "typ": {"type": "string", "enum": ["informativ", "kommersiell", "transaktionell", "navigerande"]},
        "beskrivning": _TEXT,
        "malgrupp": _TEXT,
    }, ["typ", "beskrivning", "malgrupp"]),
    "titel": _TEXT,
    "slug": _TEXT,
    "meta_titel": _TEXT,
    "meta_beskrivning": _TEXT,
    "dokument": _DOKUMENT,
    "bilder": {"type": "array", "items": _objekt({"alt": _TEXT, "bildtext": _TEXT, "prompt": _TEXT}, ["alt", "bildtext", "prompt"])},
}, ["sokintention", "titel", "slug", "meta_titel", "meta_beskrivning", "dokument", "bilder"])

ANTAL_BILDER = 4


class GenereringsFel(Exception):
    """Fel med ett meddelande som kan visas för användaren."""


def _tidigare_text(tidigare: dict) -> str:
    """Den tidigare versionen: dokumentet för strukturerade artiklar, annars HTML:en."""
    if isinstance(tidigare.get("dokument"), dict):
        return json.dumps(tidigare["dokument"], ensure_ascii=False)
    return tidigare.get("html", "")


def _anvandarprompt(huvudsokord: str, sekundara: list[str], andringar: str | None = None, tidigare: dict | None = None,
                    lankmal: list[dict] | None = None) -> str:
    sek = "\n".join(f"- {s}" for s in sekundara) or "- (inga angivna, välj själv relevanta närliggande termer)"
    prompt = f"Skriv en komplett artikel.\n\nHuvudsökord: {huvudsokord}\n\nSekundära sökord:\n{sek}"
    if lankmal:
        mal = "\n".join(f"- {m['titel']}: {m['url']}" for m in lankmal)
        prompt += (
            "\n\nInternlänkar: lägg in 3–6 kontextuella länkar i brödtexten (<a href=\"URL\">ankartext</a>) där de "
            "hjälper läsaren. Länka ENBART till adresserna nedan och skriv adressen exakt. Ankartexten ska vara naturlig, "
            "beskrivande och varierad (aldrig ”klicka här” eller exakt samma fras flera gånger). Länka varje adress högst en "
            "gång, inte i rubriker eller innehållsförteckningen. Passar ingen adress naturligt, länka färre. "
            "De första adresserna är prioriterade.\n" + mal
        )
    if andringar:
        prompt += f"\n\nRedaktören har granskat en tidigare version och begärt ändringar:\n<andringar>\n{andringar}\n</andringar>"
        if tidigare:
            prompt += (
                "\n\nTidigare version (behåll det som fungerar och åtgärda ändringarna):\n<tidigare_version>\n"
                f"Titel: {tidigare.get('titel', '')}\n\n{_tidigare_text(tidigare)}\n</tidigare_version>"
            )
    return prompt


def _artikel_fran_json(data: dict, huvudsokord: str, sekundara: list[str]) -> dict:
    bilder = list(data.get("bilder") or [])[:ANTAL_BILDER]
    while len(bilder) < ANTAL_BILDER:
        bilder.append({"alt": huvudsokord, "bildtext": "", "prompt": f"Editorial photo about {huvudsokord}, Scandinavian home"})
    # Designsystemet kontrollerar strukturen och placerar bilder som AI:n inte placerat (design.normalisera).
    return design.bygg({
        "titel": data["titel"].strip(),
        "slug": slugify(data.get("slug") or huvudsokord) or slugify(huvudsokord),
        "meta_titel": data["meta_titel"].strip(),
        "meta_beskrivning": data["meta_beskrivning"].strip(),
        "dokument": data["dokument"],
        "bilder": [
            {"id": i + 1, "alt": b["alt"].strip(), "bildtext": b["bildtext"].strip(), "prompt": b["prompt"].strip(), "url": None}
            for i, b in enumerate(bilder)
        ],
        "sokintention": data.get("sokintention"),
        "huvudsokord": huvudsokord,
        "sekundara_sokord": sekundara,
        "demo": False,
    }, fordela_bilder=True)


def _slutlig_text(message) -> str:
    """Texten i svaret. Om en reservmodell tagit över efter ett avbrott används bara dess del."""
    block = list(message.content)
    sista_reserv = max((i for i, b in enumerate(block) if b.type == "fallback"), default=-1)
    return "".join(b.text for b in block[sista_reserv + 1:] if b.type == "text")


async def generera_artikel(
    huvudsokord: str, sekundara: list[str], andringar: str | None = None, tidigare: dict | None = None,
    lankmal: list[dict] | None = None,
) -> AsyncIterator[dict]:
    client = anthropic.AsyncAnthropic(api_key=config.settings.anthropic_api_key, max_retries=3)
    yield {"typ": "steg", "steg": 0}
    skriver = False
    tecken = 0
    try:
        async with client.messages.stream(
            model=config.settings.claude_model,
            max_tokens=64000,
            thinking={"type": "adaptive"},
            output_config={
                "effort": config.settings.claude_effort,
                "format": {"type": "json_schema", "schema": ARTIKEL_SCHEMA},
            },
            system=[{"type": "text", "text": SYSTEMPROMPT, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": _anvandarprompt(huvudsokord, sekundara, andringar, tidigare, lankmal)}],
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
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError, ValueError):
        raise GenereringsFel("Svaret från Claude kunde inte tolkas. Försök igen.")
    yield {"typ": "klar", "artikel": artikel}
