"""Demoartikel som används tills AI-generering är inkopplad (steg 2).

Den bygger en artikel med samma struktur som den riktiga generatorn ska leverera,
så att gränssnitt, förhandsgranskning och redigering kan testas utan API-nycklar.
"""

import html
import re
import unicodedata


def slugify(text: str) -> str:
    text = text.lower().replace("å", "a").replace("ä", "a").replace("ö", "o")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:75].rstrip("-")


def _cap(text: str) -> str:
    return text[:1].upper() + text[1:]


def build_demo_article(huvudsokord: str, sekundara: list[str]) -> dict:
    """Demoartikel i designsystemets strukturerade format. Layouten varierar något med sökordet."""
    from . import design

    kw = huvudsokord.strip()
    kw_cap = _cap(kw)
    e = html.escape
    sek = [s.strip() for s in sekundara if s.strip()]
    sek_text = ", ".join(sek) if sek else "energi, inomhusklimat och kostnad"
    # Samma sökord ger alltid samma variant; olika sökord ger olika layout, precis som AI-artiklarna.
    variant = sum(map(ord, kw)) % 3

    def p(text: str) -> dict:
        return {"typ": "stycke", "text": text}

    forsta = {
        "id": "vad-ar", "layout": "mork" if variant != 1 else "bild", "bild": 2,
        "etikett": "Så fungerar det", "rubrik": f"Vad innebär {kw}?", "meny": "Så fungerar det",
        "block": [
            p(f"Det här är en <em>demotext</em> som visar hur artikeln kommer att se ut. När AI-genereringen är inkopplad ersätts den av en fullständig, sökordsoptimerad text om <strong>{e(kw)}</strong>."),
            p(f"Texten tar upp de sekundära sökorden naturligt: {e(sek_text)}."),
            {"typ": "underrubrik", "text": "Så fungerar det i praktiken"},
            p("Korta stycken, tydliga underrubriker och konkreta exempel gör texten lättläst för både besökare och sökmotorer."),
        ],
    }
    problem = {
        "id": "vanliga-problem", "layout": "standard", "etikett": "Vanliga utmaningar",
        "rubrik": "Vanliga problem och tecken att se upp med", "meny": "Vanliga problem",
        "ingress": "Här är några tecken på att det är dags att se över lösningen i ditt hus.",
        "block": [
            {"typ": "kort", "variant": "problem", "punkter": [
                {"rubrik": "Instängd luft", "text": "Luften känns tung, särskilt i sovrum och rum där många vistas länge."},
                {"rubrik": "Kondens på fönster", "text": "Återkommande kondens på insidan kan tyda på hög luftfuktighet."},
                {"rubrik": "Ojämn temperatur", "text": "Drag och kalla golv kan bero på hur luften rör sig i huset."},
            ] + ([
                {"rubrik": "Höga energikostnader", "text": "Värme som försvinner ut i onödan märks på elräkningen."},
                {"rubrik": "Lukt och fukt", "text": "Unken lukt kan vara ett tidigt tecken på fuktproblem."},
                {"rubrik": "Förändringar efter renovering", "text": "Tätare fönster och dörrar kan påverka husets klimat."},
            ] if variant != 2 else [])},
            {"typ": "notis", "etikett": "Viktigt:", "text": "Ett enskilt tecken bevisar inte att något är fel. Vid återkommande problem behöver orsaken undersökas."},
        ],
    }
    for_och_nack = {
        "id": "fordelar", "layout": "standard", "etikett": "Väg för- och nackdelar",
        "rubrik": "Fördelar och nackdelar", "meny": "För- och nackdelar",
        "block": [p("Det finns både vinster och saker att tänka på innan du bestämmer dig.")] + (
            [{"typ": "tabell", "kolumner": ["", "Fördelar", "Nackdelar"], "rader": [
                ["Inomhusklimat", "Bättre luft och mindre fukt", "Kan kräva ingrepp i huset"],
                ["Ekonomi", "Lägre energikostnader på sikt", "Investeringskostnad"],
                ["Underhåll", "Längre livslängd för huset", "Behöver regelbunden service"],
            ]}] if variant != 1 else [
                {"typ": "checklista", "punkter": ["Bättre inomhusklimat", "Lägre energikostnader på sikt", "Minskad risk för fukt och mögel"]},
                {"typ": "notis", "etikett": "Tänk på:", "text": "Investeringen och eventuella ingrepp i huset behöver vägas mot nyttan."},
            ]),
    }
    kostnad = {
        "id": "kostnad", "layout": "bild", "bild": 3, "etikett": "Planering och budget",
        "rubrik": "Vad kostar det?", "meny": "Kostnad",
        "block": [
            p("Priset varierar beroende på husets storlek och vilken lösning du väljer. En enkel åtgärd kostar ofta 5 000–15 000 kr, medan en helhetslösning kan landa på 80 000–150 000 kr."),
            {"typ": "underrubrik", "text": "Vad påverkar kostnaden mest?"},
            {"typ": "checklista", "punkter": ["Husets storlek och planlösning.", "Om befintliga installationer kan användas.", "Behov av håltagning eller nya kanaler.", "ROT-avdrag för arbetskostnaden."]},
            {"typ": "notis", "etikett": "Tänk på:", "text": "Ingrepp i äldre hus kan påverka fuktbalansen. Rådfråga en sakkunnig innan du gör större förändringar."},
        ],
    }
    steg = {
        "id": "steg-for-steg", "layout": "bild", "bild": 4, "etikett": "Steg för steg",
        "rubrik": "Så går du tillväga – steg för steg", "meny": "Steg för steg",
        "block": [{"typ": "steg", "punkter": [
            {"rubrik": "Inventera nuläget.", "text": "Kontrollera hur det fungerar i dag och notera problem."},
            {"rubrik": "Jämför alternativ.", "text": "Väg kostnad mot nytta och husets förutsättningar."},
            {"rubrik": "Ta in offerter.", "text": "Minst tre, och fråga efter referenser."},
            {"rubrik": "Följ upp.", "text": "Mät och utvärdera efter installationen."},
        ]}],
    }
    atgarder = {
        "id": "atgarder", "layout": "losningar", "etikett": "Praktiska lösningar",
        "rubrik": "Fyra åtgärder som ofta ger störst effekt", "meny": "Åtgärder",
        "ingress": "Börja med de enklaste kontrollerna innan du planerar större ingrepp.",
        "block": [{"typ": "kort", "variant": "losning", "punkter": [
            {"rubrik": "Kontrollera det du redan har", "text": "Se till att befintliga ventiler och don är öppna och rena."},
            {"rubrik": "Åtgärda det enkla först", "text": "Små justeringar kan räcka längre än man tror."},
            {"rubrik": "Planera helheten", "text": "Tänk på hela huset innan du väljer en ny lösning."},
            {"rubrik": "Anlita fackman", "text": "Låt en certifierad installatör bedöma större förändringar."},
        ]}],
    }
    ordning = [forsta, problem, for_och_nack, kostnad, steg, atgarder]
    if variant == 2:
        ordning = [forsta, for_och_nack, problem, kostnad, atgarder, steg]

    dokument = {
        "hero": {"etikett": "Klimathemmet / Guide",
                 "ingress": f"Funderar du på <strong>{e(kw)}</strong>? I den här guiden går vi igenom vad som är viktigt att tänka på, vilka alternativ som finns och hur du gör för att få ett bra resultat.",
                 "knapp": {"text": "Se åtgärderna", "mal": "atgarder"}},
        "intro": {
            "etikett": "Det här behöver du veta", "rubrik": f"Kort om {kw}",
            "stycken": [f"{e(kw_cap)} påverkar både inomhusklimatet och energiförbrukningen. Rätt lösning beror på husets ålder, konstruktion och nuvarande system.",
                        "Ta alltid in flera offerter och kontrollera att installatören är certifierad."],
            "fakta": {"etikett": "Snabböversikt", "rubrik": kw_cap, "punkter": [
                {"titel": "Passar för", "text": "Villor och äldre hus där inomhusklimatet behöver förbättras."},
                {"titel": "Första åtgärden", "text": "Kontrollera hur det fungerar i dag innan du bygger om."},
                {"titel": "Ungefärlig kostnad", "text": "Från några tusenlappar till över 100 000 kr."},
                {"titel": "Vid större problem", "text": "Låt en fackperson bedöma huset."},
            ]},
        },
        "sektioner": ordning,
        "sammanfattning": {"rubrik": f"Så lyckas du med {kw}", "stycken": [
            f"{e(kw_cap)} handlar om att hitta rätt lösning för just ditt hus. Utgå från nuläget, jämför alternativen och anlita certifierade installatörer."]},
        "faq": {"rubrik": f"Vanliga frågor om {kw}", "fragor": [
            {"fraga": f"Hur vet jag om {kw} behövs i mitt hus?", "svar": "Ett svar som besvarar frågan kort och tydligt."},
            {"fraga": "Kan jag göra det själv?", "svar": "Enklare delar kan du göra själv, men installationer bör utföras av fackman."},
            {"fraga": "Finns det bidrag eller avdrag?", "svar": "Ofta går det att använda ROT-avdrag för arbetskostnaden."},
            {"fraga": "Hur lång tid tar arbetet?", "svar": "Det beror på omfattningen; enklare åtgärder görs ofta på en dag."},
        ]},
    }

    bildmotiv = [
        (f"Ljust skandinaviskt hem där {kw} bidrar till ett behagligt inomhusklimat",
         f"Bright Scandinavian living room in a Swedish villa, showing the overall topic: {kw}"),
        (f"Närbild som visar hur {sek[0] if sek else kw} fungerar i ett äldre svenskt hus",
         f"Close-up of the relevant installation or building part for {sek[0] if sek else kw} in an older Swedish house"),
        ("Husägare och hantverkare går igenom kostnader vid ett ljust köksbord",
         "Homeowner and craftsman reviewing a quote with papers on a light wooden kitchen table, hands in focus"),
        (f"Hantverkare arbetar steg för steg med {kw} i en svensk villa",
         f"Craftsman working step by step on {kw} in a Swedish villa, seen from behind, natural daylight"),
    ]
    images = [{"id": i + 1, "alt": alt, "bildtext": "", "prompt": prompt, "url": None} for i, (alt, prompt) in enumerate(bildmotiv)]

    meta_title = f"{kw_cap} – guide, kostnad & tips"
    return design.bygg({
        "titel": f"{kw_cap}: så gör du rätt",
        "slug": slugify(kw),
        "meta_titel": meta_title[:60],
        "meta_beskrivning": (
            f"Allt du behöver veta om {kw}: fördelar, kostnader och steg-för-steg-guide. "
            "Läs våra tips innan du sätter igång."
        )[:155],
        "dokument": dokument,
        "bilder": images,
        "huvudsokord": kw,
        "sekundara_sokord": sek,
        "demo": True,
    })
