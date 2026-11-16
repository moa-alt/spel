"""AI-generering av artiklar med Claude.

generera_artikel() är en asynkron generator som först skickar förloppshändelser
och till sist den färdiga artikeln, så att gränssnittet kan visa vad som händer.
"""

import json
import logging
import re
from collections.abc import AsyncIterator

import anthropic

from . import config, design
from .demo import slugify

log = logging.getLogger("studio.ai_writer")

RESERV_BETA = "server-side-fallback-2026-07-01"
# Allt som liknar nycklar och tokens maskeras innan ett felmeddelande visas eller loggas.
_KANSLIGT = re.compile(
    r"sk-ant-[A-Za-z0-9_\-]{6,}|sk-(?:proj-)?[A-Za-z0-9_\-]{16,}|(?i:bearer)\s+[A-Za-z0-9._\-]{8,}"
    r"|(?i:x-api-key)[\"':=\s]+[A-Za-z0-9._\-]{6,}|[A-Za-z0-9_\-]{40,}")

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

Alla fält ska alltid finnas med. När ett fält inte används skriver du en tom sträng "", en tom lista [] eller 0 för bild.

Artikeln (fältet dokument) består av:
- hero: etikett (kort kategorirad i versaler, t.ex. "VÄRME & ENERGI / GUIDE"), ingress (artikelns första stycke: 2–4 meningar som innehåller huvudsökordet och direkt besvarar vad läsaren vill veta), knapp_text (kort uppmaning, t.ex. "Se 7 åtgärder", eller "") och knapp_mal (id för sektionen knappen leder till, eller ""). Titeln (H1) visas i heron.
- intro: etikett, rubrik (H2), stycken (2–4 stycken som sätter in läsaren i ämnet), och en mörkgrön snabböversikt: fakta_etikett, fakta_rubrik och fakta (3–5 punkter med titel och kort text).
- sektioner: 5–9 avsnitt i den ordning läsaren behöver dem. Varje sektion har id (slug), layout, etikett (kort, i versaler), rubrik (H2), meny (1–3 ord till innehållsförteckningen), ingress (eller ""), bild (2, 3 eller 4, eller 0 för ingen bild) och en lista med block. Minst en rubrik innehåller huvudsökordet.
- sammanfattning: rubrik och 1–3 stycken.
- faq: rubrik och fragor: 4–8 vanliga frågor, var och en med fraga och svar (2–4 meningar).
- relaterade_rubrik: rubrik för relaterade guider (själva länkarna väljs automatiskt).
Innehållsförteckning och relaterade artiklar skapas automatiskt; skriv dem inte själv.

## Layouter – välj efter innehållet
- standard: vanligt textavsnitt.
- mork: mörkgrön sektion i full bredd med bild bredvid texten. Passar när något förklaras, t.ex. hur ett system fungerar. Högst två per artikel.
- bild: text och bild sida vid sida; sidan växlar automatiskt. Passar avsnitt där en bild gör texten tydligare.
- losningar: gräddvit sektion i full bredd för åtgärder och rekommendationer, oftast med block av typen kort_losning.
Låt inte två sektioner i full bredd (mork, losningar) komma direkt efter varandra. Strukturen ska följa just det här ämnet; använd inte samma ordning och samma komponenter i varje artikel.

## Block – välj de som tillför något
Varje block har fälten typ, rubrik, text, punkter och rader. Fyll i de fält blocktypen använder och lämna de övriga tomma ("" eller []).
- stycke: text på 2–4 meningar.
- underrubrik: text = H3:ans rubrik inom sektionen.
- lista: punktlista. checklista: punkter med bock för saker att kontrollera, tänka på eller ta med. Varje punkt har text (rubrik "").
- steg: numrerade steg, för instruktioner som görs i ordning. Varje punkt har rubrik och text.
- notis: informationsruta. rubrik = etikett (t.ex. "Viktigt:", "Tips:", "Kom ihåg:", "Rekommendation:") och text.
- kort_problem och kort_losning: 3–6 kort i punkter, med rubrik och text. kort_problem för problem, tecken och orsaker; kort_losning för åtgärder och rekommendationer.
- tabell: jämförelse i rader. Första raden är kolumnrubrikerna (2–4 kolumner), följande 3–8 rader innehållet. Använd bara när läsaren faktiskt jämför alternativ, system eller kostnader.
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


def _alla(egenskaper: dict) -> dict:
    """Ett objekt där alla egenskaper är obligatoriska. Valfria egenskaper gör grammatiken mycket större."""
    return _objekt(egenskaper, list(egenskaper))


_LISTA = lambda items: {"type": "array", "items": items}  # noqa: E731
_PUNKT = _alla({"rubrik": _TEXT, "text": _TEXT})

# Strukturerade svar kompileras till en grammatik som har en storleksgräns. Schemat hålls därför platt och
# helt utan valfria egenskaper: varje block har samma fem fält, och oanvända fält är tomma. Den nästlade
# strukturen med valfria fält (sektioner[].block[] med sex valfria egenskaper) gav felet
# "The compiled grammar is too large". dokument_fran_ai() gör om svaret till designsystemets format.
_BLOCK = _alla({
    "typ": {"type": "string", "enum": ["stycke", "underrubrik", "lista", "checklista", "steg", "notis",
                                       "kort_problem", "kort_losning", "tabell"]},
    "rubrik": _TEXT,
    "text": _TEXT,
    "punkter": _LISTA(_PUNKT),
    "rader": _LISTA(_LISTA(_TEXT)),
})

_SEKTION = _alla({
    "id": _TEXT,
    "layout": {"type": "string", "enum": ["standard", "mork", "bild", "losningar"]},
    "etikett": _TEXT,
    "rubrik": _TEXT,
    "meny": _TEXT,
    "ingress": _TEXT,
    "bild": {"type": "integer", "enum": [0, 2, 3, 4]},
    "block": _LISTA(_BLOCK),
})

_DOKUMENT = _alla({
    "hero": _alla({"etikett": _TEXT, "ingress": _TEXT, "knapp_text": _TEXT, "knapp_mal": _TEXT}),
    "intro": _alla({"etikett": _TEXT, "rubrik": _TEXT, "stycken": _LISTA(_TEXT), "fakta_etikett": _TEXT,
                    "fakta_rubrik": _TEXT, "fakta": _LISTA(_alla({"titel": _TEXT, "text": _TEXT}))}),
    "sektioner": _LISTA(_SEKTION),
    "sammanfattning": _alla({"rubrik": _TEXT, "stycken": _LISTA(_TEXT)}),
    "faq": _alla({"rubrik": _TEXT, "fragor": _LISTA(_alla({"fraga": _TEXT, "svar": _TEXT}))}),
    "relaterade_rubrik": _TEXT,
})


def _artikelschema(dokument: dict) -> dict:
    return _alla({
        "sokintention": _alla({
            "typ": {"type": "string", "enum": ["informativ", "kommersiell", "transaktionell", "navigerande"]},
            "beskrivning": _TEXT,
            "malgrupp": _TEXT,
        }),
        "titel": _TEXT,
        "slug": _TEXT,
        "meta_titel": _TEXT,
        "meta_beskrivning": _TEXT,
        "dokument": dokument,
        "bilder": _LISTA(_alla({"alt": _TEXT, "bildtext": _TEXT, "prompt": _TEXT})),
    })


ARTIKEL_SCHEMA = _artikelschema(_DOKUMENT)
# Reservformat om API:et ändå avvisar grammatiken: dokumentet skrivs som en JSON-sträng med samma
# struktur och kontrolleras helt av design.normalisera. Grammatiken blir då mycket liten.
ENKELT_SCHEMA = _artikelschema(_TEXT)
ENKEL_INSTRUKTION = ("\n\nSkriv fältet dokument som en JSON-sträng (inte som ett objekt) med exakt den struktur som beskrivs "
                     "för dokument: hero, intro, sektioner med block, sammanfattning, faq och relaterade_rubrik.")

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


def _text_eller_tom(v) -> str:
    return v if isinstance(v, str) else ""


def _block_fran_ai(b: dict) -> dict | None:
    """Ett block i det platta AI-formatet som ett block i designsystemets format."""
    if not isinstance(b, dict):
        return None
    typ, punkter = b.get("typ"), [p for p in b.get("punkter") or [] if isinstance(p, dict)]
    if typ in ("kort_problem", "kort_losning"):
        return {"typ": "kort", "variant": "losning" if typ == "kort_losning" else "problem", "punkter": punkter}
    if typ == "tabell":
        rader = [r for r in b.get("rader") or [] if isinstance(r, list)]
        return {"typ": "tabell", "kolumner": rader[0] if rader else [], "rader": rader[1:]}
    if typ == "notis":
        return {"typ": "notis", "etikett": _text_eller_tom(b.get("rubrik")), "text": _text_eller_tom(b.get("text"))}
    if typ in ("lista", "checklista"):
        return {"typ": typ, "punkter": [p.get("text") or p.get("rubrik") or "" for p in punkter]}
    if typ == "steg":
        return {"typ": "steg", "punkter": punkter}
    if typ == "underrubrik":
        return {"typ": "underrubrik", "text": _text_eller_tom(b.get("text")) or _text_eller_tom(b.get("rubrik"))}
    return {"typ": "stycke", "text": _text_eller_tom(b.get("text"))}


def dokument_fran_ai(d: dict) -> dict:
    """Gör om AI:ns platta svar till designsystemets dokument. design.normalisera kontrollerar resten."""
    d = d if isinstance(d, dict) else {}
    hero, intro = d.get("hero") or {}, d.get("intro") or {}
    return {
        "hero": {"etikett": hero.get("etikett"), "ingress": hero.get("ingress"),
                 "knapp": {"text": hero.get("knapp_text"), "mal": hero.get("knapp_mal")}},
        "intro": {"etikett": intro.get("etikett"), "rubrik": intro.get("rubrik"), "stycken": intro.get("stycken"),
                  "fakta": {"etikett": intro.get("fakta_etikett"), "rubrik": intro.get("fakta_rubrik"),
                            "punkter": intro.get("fakta")}},
        "sektioner": [
            {"id": s.get("id"), "layout": s.get("layout"), "etikett": s.get("etikett"), "rubrik": s.get("rubrik"),
             "meny": s.get("meny"), "ingress": s.get("ingress"), "bild": s.get("bild") or None,
             "block": [b for b in (_block_fran_ai(b) for b in s.get("block") or []) if b]}
            for s in d.get("sektioner") or [] if isinstance(s, dict)
        ],
        "sammanfattning": d.get("sammanfattning"),
        "faq": d.get("faq"),
        "relaterade_rubrik": d.get("relaterade_rubrik"),
    }


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
        "dokument": dokument_fran_ai(data["dokument"]),
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


def _klient() -> anthropic.AsyncAnthropic:
    return anthropic.AsyncAnthropic(api_key=config.settings.anthropic_api_key, max_retries=3)


def anropsparametrar(prompt: str, reservmodell: bool, enkelt_format: bool = False) -> dict:
    """Parametrarna till Messages API. Nyckeln skickas av SDK:n i ett huvud, aldrig här.

    enkelt_format: dokumentet skrivs som en JSON-sträng (reservväg om grammatiken skulle bli för stor).
    """
    s = config.settings
    schema = ENKELT_SCHEMA if enkelt_format else ARTIKEL_SCHEMA
    parametrar = {
        "model": s.claude_model,
        "max_tokens": 64000,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": s.claude_effort, "format": {"type": "json_schema", "schema": schema}},
        "system": [{"type": "text", "text": SYSTEMPROMPT, "cache_control": {"type": "ephemeral"}}],
        "messages": [{"role": "user", "content": prompt + (ENKEL_INSTRUKTION if enkelt_format else "")}],
    }
    if reservmodell:
        # Om modellen avböjer tar en reservmodell över automatiskt (beta).
        parametrar["extra_headers"] = {"anthropic-beta": RESERV_BETA}
        parametrar["extra_body"] = {"fallbacks": "default"}
    return parametrar


def maskera(text: str) -> str:
    """Tar bort allt som liknar nycklar och tokens, även den konfigurerade nyckeln."""
    nyckel = config.settings.anthropic_api_key
    if nyckel:
        text = text.replace(nyckel, "[dolt]")
    return _KANSLIGT.sub("[dolt]", text)


def felorsak(e: anthropic.APIStatusError) -> tuple[str, str, str | None]:
    """API:ets egen förklaring: (feltyp, meddelande, request-id). Meddelandet är maskerat och förkortat."""
    fel = e.body.get("error") if isinstance(e.body, dict) else None
    typ = str((fel or {}).get("type") or e.type or "")
    meddelande = str((fel or {}).get("message") or "")
    return maskera(typ), maskera(" ".join(meddelande.split()))[:500], e.request_id


def _ar_grammatikfel(meddelande: str) -> bool:
    m = meddelande.lower()
    return "grammar" in m or "too complex" in m or "compil" in m


def _ar_reservfel(meddelande: str) -> bool:
    m = meddelande.lower()
    return "anthropic-beta" in m or "fallback" in m


def _atgard(status: int, typ: str, meddelande: str) -> str:
    """Konkret åtgärd för kända orsaker till fel från Claude."""
    m = meddelande.lower()
    if "credit balance" in m or typ == "billing_error" or status == 402:
        return "Anthropic-kontot saknar krediter. Fyll på under Plans & Billing i Anthropic Console."
    if _ar_reservfel(meddelande):
        return "Kontot har inte tillgång till reservmodellerna (beta). Sätt STUDIO_CLAUDE_RESERVMODELL=av och starta om servern."
    if m.startswith("model") or status == 404:
        return f"Modellen {config.settings.claude_model} finns inte eller är inte tillgänglig för kontot. Kontrollera STUDIO_CLAUDE_MODEL."
    if "output_config" in m or "schema" in m:
        return "Claude godtog inte artikelns svarsformat (JSON-schemat). Det är ett fel i studion; skicka felmeddelandet till utvecklaren."
    if "effort" in m:
        return "Kontrollera STUDIO_CLAUDE_EFFORT (low, medium, high, xhigh eller max)."
    if "too long" in m or "max_tokens" in m:
        return "Anropet blev för stort. Försök med färre sekundära sökord eller kortare ändringsönskemål."
    return ""


def _api_fel(e: anthropic.APIStatusError) -> GenereringsFel:
    typ, meddelande, request_id = felorsak(e)
    log.warning("Claude svarade %s %s: %s (request-id %s)", e.status_code, typ, meddelande or "-", request_id or "-")
    delar = [f"Claude svarade med fel {e.status_code}" + (f" ({typ})" if typ else "") + (f": {meddelande}" if meddelande else ".")]
    if atgard := _atgard(e.status_code, typ, meddelande):
        delar.append(atgard)
    if request_id:
        delar.append(f"Request-id: {request_id}")
    return GenereringsFel(" ".join(d if d.endswith((".", ":")) else d + "." for d in delar))


async def generera_artikel(
    huvudsokord: str, sekundara: list[str], andringar: str | None = None, tidigare: dict | None = None,
    lankmal: list[dict] | None = None,
) -> AsyncIterator[dict]:
    client = _klient()
    prompt = _anvandarprompt(huvudsokord, sekundara, andringar, tidigare, lankmal)
    reservmodell = config.settings.claude_reservmodell
    enkelt_format = False
    yield {"typ": "steg", "steg": 0}
    skriver = False
    tecken = 0
    while True:
        try:
            async with client.messages.stream(**anropsparametrar(prompt, reservmodell, enkelt_format)) as stream:
                async for event in stream:
                    if event.type == "content_block_start" and event.content_block.type == "text" and not skriver:
                        skriver = True
                        yield {"typ": "steg", "steg": 1}
                    elif event.type == "content_block_delta" and event.delta.type == "text_delta":
                        tecken += len(event.delta.text)
                        if tecken // 2000 != (tecken - len(event.delta.text)) // 2000:
                            yield {"typ": "framsteg", "tecken": tecken}
                message = await stream.get_final_message()
            break
        except anthropic.BadRequestError as e:
            _, meddelande, request_id = felorsak(e)
            if not enkelt_format and not skriver and _ar_grammatikfel(meddelande):
                # Svarsformatet avvisades som för komplext: nytt försök med dokumentet som JSON-sträng.
                log.warning("Claude godtog inte svarsformatet (%s, request-id %s); försöker med det enkla formatet.",
                            meddelande, request_id or "-")
                enkelt_format = True
                continue
            if reservmodell and not skriver and _ar_reservfel(meddelande):
                # Reservmodellerna är valfria: godtas de inte av kontot görs ett nytt försök utan dem.
                log.warning("Claude godtog inte reservmodellerna (%s, request-id %s); försöker utan dem. "
                            "Sätt STUDIO_CLAUDE_RESERVMODELL=av för att slippa det här försöket.", meddelande, request_id or "-")
                reservmodell = False
                continue
            raise _api_fel(e)
        except anthropic.AuthenticationError:
            raise GenereringsFel("API-nyckeln godkändes inte. Kontrollera ANTHROPIC_API_KEY och starta om servern.")
        except anthropic.PermissionDeniedError as e:
            _, meddelande, request_id = felorsak(e)
            raise GenereringsFel(f"API-nyckeln saknar behörighet{': ' + meddelande if meddelande else ''}. "
                                 f"Kontrollera nyckeln och organisationen i Anthropic Console.{' Request-id: ' + request_id + '.' if request_id else ''}")
        except anthropic.RateLimitError:
            raise GenereringsFel("För många anrop just nu. Vänta en stund och försök igen.")
        except anthropic.APIStatusError as e:
            if e.status_code >= 500:
                log.warning("Claude svarade %s (request-id %s)", e.status_code, e.request_id or "-")
                raise GenereringsFel(f"Claude är tillfälligt överbelastad ({e.status_code}). Försök igen om en stund.")
            raise _api_fel(e)
        except anthropic.APIConnectionError:
            raise GenereringsFel("Kunde inte nå Claude. Kontrollera serverns internetanslutning.")

    if message.stop_reason == "refusal":
        raise GenereringsFel("Claude avböjde att skriva om det här ämnet. Prova att formulera sökordet annorlunda.")
    if message.stop_reason == "max_tokens":
        raise GenereringsFel("Svaret blev för långt och klipptes av. Försök igen.")

    yield {"typ": "steg", "steg": 3}
    try:
        data = json.loads(_slutlig_text(message))
        if isinstance(data.get("dokument"), str):
            data["dokument"] = json.loads(data["dokument"])
        artikel = _artikel_fran_json(data, huvudsokord, sekundara)
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError, ValueError):
        raise GenereringsFel("Svaret från Claude kunde inte tolkas. Försök igen.")
    yield {"typ": "klar", "artikel": artikel}
