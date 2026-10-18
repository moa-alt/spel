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
    kw = huvudsokord.strip()
    kw_cap = _cap(kw)
    e = html.escape
    sek = [s.strip() for s in sekundara if s.strip()]
    sek_text = ", ".join(sek) if sek else "energi, inomhusklimat och kostnad"

    sections = [
        ("vad-ar", f"Vad innebär {kw}?"),
        ("fordelar", "Fördelar och nackdelar"),
        ("kostnad", "Vad kostar det?"),
        ("steg-for-steg", "Så går du tillväga – steg för steg"),
        ("vanliga-fragor", "Vanliga frågor"),
    ]
    toc = "".join(f'<li><a href="#{sid}">{e(rubrik)}</a></li>' for sid, rubrik in sections)

    sek_lista = "".join(f"<li>{e(_cap(s))}</li>" for s in sek) or "<li>Energieffektivitet</li><li>Inomhusklimat</li>"

    body = f"""
<p class="kh-ingress">Funderar du på <strong>{e(kw)}</strong>? I den här guiden går vi igenom vad som är viktigt att tänka på, vilka alternativ som finns och hur du gör för att få ett bra resultat – med fokus på {e(sek_text)}.</p>

<div class="kh-snabbfakta">
  <p class="kh-box-rubrik">Det viktigaste i korthet</p>
  <ul>
    <li>{e(kw_cap)} påverkar både inomhusklimatet och energiförbrukningen.</li>
    <li>Rätt lösning beror på husets ålder, konstruktion och nuvarande system.</li>
    <li>Ta alltid in flera offerter och kontrollera att installatören är certifierad.</li>
  </ul>
</div>

<nav class="kh-toc" aria-label="Innehåll">
  <p class="kh-box-rubrik">Innehåll</p>
  <ol>{toc}</ol>
</nav>

<!--BILD:1-->

<h2 id="vad-ar">{e(sections[0][1])}</h2>
<!--BILD:2-->
<p>Det här är en <em>demotext</em> som visar hur artikeln kommer att se ut. När AI-genereringen är inkopplad ersätts den av en fullständig, sökordsoptimerad text som utgår från sökintentionen bakom <strong>{e(kw)}</strong>.</p>
<p>Texten kommer att behandla de sekundära sökorden naturligt i löptexten och i underrubriker:</p>
<ul>{sek_lista}</ul>

<h3>Så fungerar det i praktiken</h3>
<p>Ett stycke med fördjupning. Korta stycken, tydliga underrubriker och konkreta exempel gör texten lättläst både för besökare och sökmotorer.</p>

<div class="kh-tips">
  <p class="kh-box-rubrik">Tips</p>
  <p>Börja med en enkel kontroll av nuläget innan du investerar – ofta räcker mindre åtgärder längre än man tror.</p>
</div>

<h2 id="fordelar">{e(sections[1][1])}</h2>
<div class="kh-procon">
  <div class="kh-pro">
    <p class="kh-box-rubrik">Fördelar</p>
    <ul><li>Bättre inomhusklimat</li><li>Lägre energikostnader på sikt</li><li>Minskad risk för fukt och mögel</li></ul>
  </div>
  <div class="kh-con">
    <p class="kh-box-rubrik">Nackdelar</p>
    <ul><li>Investeringskostnad</li><li>Kan kräva ingrepp i huset</li><li>Behöver underhåll</li></ul>
  </div>
</div>

<h2 id="kostnad">{e(sections[2][1])}</h2>
<!--BILD:3-->
<p>Priset varierar beroende på husets storlek och vilken lösning du väljer. Tabellen visar ungefärliga nivåer.</p>
<div class="kh-tabell-wrap">
<table class="kh-tabell">
  <thead><tr><th>Alternativ</th><th>Ungefärlig kostnad</th><th>Passar för</th></tr></thead>
  <tbody>
    <tr><td>Enkel åtgärd</td><td>5 000–15 000 kr</td><td>Mindre förbättringar</td></tr>
    <tr><td>Mellanalternativ</td><td>20 000–60 000 kr</td><td>De flesta villor</td></tr>
    <tr><td>Helhetslösning</td><td>80 000–150 000 kr</td><td>Större renoveringar</td></tr>
  </tbody>
</table>
</div>

<div class="kh-varning">
  <p class="kh-box-rubrik">Tänk på</p>
  <p>Ingrepp i äldre hus kan påverka fuktbalansen. Rådfråga en sakkunnig innan du gör större förändringar.</p>
</div>

<h2 id="steg-for-steg">{e(sections[3][1])}</h2>
<!--BILD:4-->
<ol class="kh-steg">
  <li><strong>Inventera nuläget.</strong> Kontrollera hur det fungerar i dag och notera problem.</li>
  <li><strong>Jämför alternativ.</strong> Väg kostnad mot nytta och husets förutsättningar.</li>
  <li><strong>Ta in offerter.</strong> Minst tre, och fråga efter referenser.</li>
  <li><strong>Följ upp.</strong> Mät och utvärdera efter installationen.</li>
</ol>

<h2 id="vanliga-fragor">{e(sections[4][1])}</h2>
<div class="kh-faq">
  <details><summary>Hur vet jag om {e(kw)} behövs i mitt hus?</summary><p>Ett svar som besvarar frågan kort och tydligt.</p></details>
  <details><summary>Kan jag göra det själv?</summary><p>Enklare delar kan du göra själv, men installationer bör utföras av fackman.</p></details>
  <details><summary>Finns det bidrag eller avdrag?</summary><p>Ofta går det att använda ROT-avdrag för arbetskostnaden.</p></details>
</div>

<div class="kh-sammanfattning">
  <p class="kh-box-rubrik">Sammanfattning</p>
  <p>{e(kw_cap)} handlar om att hitta rätt lösning för just ditt hus. Utgå från nuläget, jämför alternativen och anlita certifierade installatörer.</p>
</div>
""".strip()

    # (alt-text, motiv till bildtjänsten) för huvudbilden och de tre avsnitten.
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
    images = [
        {"id": i + 1, "alt": alt, "bildtext": "", "prompt": prompt, "url": None}
        for i, (alt, prompt) in enumerate(bildmotiv)
    ]

    meta_title = f"{kw_cap} – guide, kostnad & tips"
    return {
        "titel": f"{kw_cap}: så gör du rätt",
        "slug": slugify(kw),
        "meta_titel": meta_title[:60],
        "meta_beskrivning": (
            f"Allt du behöver veta om {kw}: fördelar, kostnader och steg-för-steg-guide. "
            "Läs våra tips innan du sätter igång."
        )[:155],
        "html": body,
        "bilder": images,
        "huvudsokord": kw,
        "sekundara_sokord": sek,
        "demo": True,
    }
