# Klimathemmet AI Studio

En webbapp för SEO-produktion. Du anger ett huvudsökord och sekundära sökord, och studion tar fram en artikel med fyra bilder i WebP-format, metatitel, metabeskrivning, URL-slug och alt-texter. Du granskar och redigerar artikeln och skickar den sedan till WordPress som **utkast**.

## Status

| Steg | Innehåll | Status |
|---|---|---|
| 1 | Gränssnitt, förhandsgranskning, redigering, SEO-kontroll, export av WordPress-kod | ✅ Klart |
| 2 | AI-genererad artikel utifrån sökintention (Claude) | ✅ Klart |
| 3 | 4 AI-bilder → komprimerad WebP | Nästa steg |
| 4 | Skicka till WordPress som utkast (inkl. metadata för Yoast/Rank Math) | Planerat |

## Kom igång

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python scripts/spara_hemlighet.py   # lägger in ANTHROPIC_API_KEY säkert
git config core.hooksPath scripts/git-hooks
uvicorn app.main:app --reload
```

Öppna sedan http://localhost:8000.

## AI-generering (steg 2)

Med `ANTHROPIC_API_KEY` i `.env` skriver Claude artikeln. Utan nyckel visas en demotext.

- Claude analyserar först sökintentionen (typ, beskrivning och målgrupp). Den visas i SEO-fliken.
- Svaret kommer som strukturerad JSON: titel, slug, metatitel, metabeskrivning, HTML och fyra bilder med alt-text, bildtext och bildprompt.
- HTML:en följer designens komponenter (`kh-snabbfakta`, `kh-toc`, `kh-tips` med flera) och passerar en tillåtelselista som tar bort skript, stilar, `on*`-attribut och osäkra länkar.
- Förloppet strömmas till gränssnittet medan Claude arbetar. En artikel tar normalt 1–3 minuter.
- Om Claude avböjer en förfrågan tar en reservmodell över automatiskt (`fallbacks: "default"`).
- Valfria inställningar: `STUDIO_CLAUDE_MODEL` (standard `claude-opus-5-5`) och `STUDIO_CLAUDE_EFFORT` (`low`–`max`, standard `high`). Lägre effort går snabbare och kostar mindre.

Prompten finns i `app/ai_writer.py` (`SYSTEMPROMPT`). Där justerar du ton, längd och struktur.

## Funktioner i steg 1

- **Brief:** huvudsökord och sekundära sökord (lägg till med Enter eller komma).
- **Förhandsgranskning:** artikeln visas med Klimathemmet-designen, på dator- eller mobilbredd.
- **Redigering:** direkt i förhandsvisningen, i HTML-fliken eller i rubrikfältet. Ändringar sparas automatiskt i webbläsaren.
- **SEO-fliken:** metatitel och metabeskrivning med teckenräknare, URL-slug och en Google-förhandsvisning.
- **Bilder-fliken:** alt-text, bildtext och bildbeskrivning för de fyra bilderna.
- **SEO-kontroll:** kontrollerar bland annat sökord i H1, metadata, slug, första stycket, underrubriker, sekundära sökord, alt-texter och textlängd.
- **WordPress-kod:** färdig kod för ett *Anpassad HTML*-block, med CSS:en inbäddad och scopad till `.kh-artikel`.

## Säkerhet och API-nycklar

### Lägg in nyckeln (rekommenderat sätt)

```bash
python scripts/spara_hemlighet.py              # frågar efter ANTHROPIC_API_KEY
```

Klistra in nyckeln när du får frågan. Inget syns på skärmen. Nyckeln sparas i `~/.config/klimathemmet-studio/ANTHROPIC_API_KEY`, som bara ditt konto kan läsa (`chmod 600`), och testas direkt mot Anthropic. Den hamnar inte i projektmappen, git, terminalhistoriken eller loggar. Starta sedan om servern.

Samma skript fungerar för `OPENAI_API_KEY`, `WP_APP_PASSWORD` och `STUDIO_PASSWORD`.

Servern letar efter en hemlighet i den här ordningen:
1. miljövariabeln eller `.env`
2. filen som `<NAMN>_FILE` pekar på (för Docker- eller systemd-secrets)
3. `~/.config/klimathemmet-studio/<NAMN>`

### Skydd som är inbyggt

- Webbläsaren får bara veta *om* en integration är konfigurerad, aldrig själva värdena.
- Hemligheterna visas aldrig i utskrifter av inställningsobjektet (`repr=False`).
- `.env` är undantagen från git. Commit-spärren i `scripts/git-hooks/pre-commit` stoppar commits som innehåller `.env` eller något som ser ut som en API-nyckel. Aktivera den en gång per klon: `git config core.hooksPath scripts/git-hooks`.
- Sätt `STUDIO_USER` och `STUDIO_PASSWORD` när appen körs på en server, så skyddas hela studion med lösenord.
- Förhandsvisningen körs i en sandlådad iframe där skript är avstängda.

### Claude Code på webben

Lägg **inte** nyckeln under *Environment variables* i molnmiljön. De är synliga för alla som kan redigera miljön.

Molnmiljön behöver inte nyckeln alls. Den används för att utveckla koden, och AI-flödet testas där mot ett låtsas-API. Själva appen körs på din dator eller din server, och där läggs nyckeln in enligt ovan.

### Drift på en server

Använd värdtjänstens egen hemlighetshantering, till exempel *Secrets* hos Render, Railway eller Fly.io, `systemd` med `LoadCredential=`, eller Docker secrets med `ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key`. Kör alltid bakom HTTPS och med `STUDIO_USER` och `STUDIO_PASSWORD` satta.

## Struktur

```
scripts/
  spara_hemlighet.py   Sparar nycklar säkert utanför projektet
  git-hooks/pre-commit Stoppar commits med nycklar
app/
  main.py          FastAPI-server och API
  config.py        Läser inställningar och hemligheter från .env
  ai_writer.py     Prompt och anrop till Claude
  sanitize.py      Rensar AI:ns HTML med en tillåtelselista
  demo.py          Demoartikel när ingen API-nyckel finns
  render.py        Bygger WordPress-koden
  static/
    index.html     Gränssnittet
    app.css        Studions design
    app.js         Logik för gränssnittet
    artikel.css    Artikeldesignen som följer med till WordPress
```

## Om designen

`app/static/artikel.css` innehåller artikelns design. Referenssidan (klimathemmet.se/ventilation-i-gamla-hus/) gick inte att nå från utvecklingsmiljön. Den här första versionen bygger därför på ett vanligt upplägg för guideartiklar: ingress, faktaruta, innehållsförteckning, tips- och varningsrutor, för- och nackdelar, tabell, numrerade steg, vanliga frågor och sammanfattning. Klistra in sidans HTML-källkod (eller temats CSS) så matchar vi designen exakt.
