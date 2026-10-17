# Klimathemmet AI Studio

En webbapp för SEO-produktion. Du anger ett huvudsökord och sekundära sökord, och studion tar fram en artikel med fyra bilder i WebP-format, metatitel, metabeskrivning, URL-slug och alt-texter. Du granskar och redigerar artikeln och skickar den sedan till WordPress som **utkast**.

## Status

| Steg | Innehåll | Status |
|---|---|---|
| 1 | Gränssnitt, förhandsgranskning, redigering, SEO-kontroll, export av WordPress-kod | ✅ Klart (med demotext) |
| 2 | AI-genererad artikel utifrån sökintention (Claude) | Nästa steg |
| 3 | 4 AI-bilder → komprimerad WebP | Planerat |
| 4 | Skicka till WordPress som utkast (inkl. metadata för Yoast/Rank Math) | Planerat |

## Kom igång

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # fyll i nycklar när vi kommer till steg 2–4
uvicorn app.main:app --reload
```

Öppna sedan http://localhost:8000.

## Funktioner i steg 1

- **Brief:** huvudsökord och sekundära sökord (lägg till med Enter eller komma).
- **Förhandsgranskning:** artikeln visas med Klimathemmet-designen, på dator- eller mobilbredd.
- **Redigering:** direkt i förhandsvisningen, i HTML-fliken eller i rubrikfältet. Ändringar sparas automatiskt i webbläsaren.
- **SEO-fliken:** metatitel och metabeskrivning med teckenräknare, URL-slug och en Google-förhandsvisning.
- **Bilder-fliken:** alt-text, bildtext och bildbeskrivning för de fyra bilderna.
- **SEO-kontroll:** kontrollerar bland annat sökord i H1, metadata, slug, första stycket, underrubriker, sekundära sökord, alt-texter och textlängd.
- **WordPress-kod:** färdig kod för ett *Anpassad HTML*-block, med CSS:en inbäddad och scopad till `.kh-artikel`.

## Säkerhet

- API-nycklar och WordPress-uppgifter ligger bara i `.env` på servern. Webbläsaren får bara veta *om* en integration är konfigurerad, aldrig själva värdena.
- `.env` är undantagen från git.
- Sätt `STUDIO_USER` och `STUDIO_PASSWORD` när appen körs på en server, så skyddas hela studion med lösenord.
- Förhandsvisningen körs i en sandlådad iframe där skript är avstängda.

## Struktur

```
app/
  main.py          FastAPI-server och API
  config.py        Läser inställningar och hemligheter från .env
  demo.py          Demoartikel (ersätts av AI i steg 2)
  render.py        Bygger WordPress-koden
  static/
    index.html     Gränssnittet
    app.css        Studions design
    app.js         Logik för gränssnittet
    artikel.css    Artikeldesignen som följer med till WordPress
```

## Om designen

`app/static/artikel.css` innehåller artikelns design. Referenssidan (klimathemmet.se/ventilation-i-gamla-hus/) gick inte att nå från utvecklingsmiljön. Den här första versionen bygger därför på ett vanligt upplägg för guideartiklar: ingress, faktaruta, innehållsförteckning, tips- och varningsrutor, för- och nackdelar, tabell, numrerade steg, vanliga frågor och sammanfattning. Klistra in sidans HTML-källkod (eller temats CSS) så matchar vi designen exakt.
