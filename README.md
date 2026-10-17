# Klimathemmet AI Studio

En webbapp för SEO-produktion. Du anger ett huvudsökord och sekundära sökord, och studion tar fram en artikel med fyra bilder i WebP-format, metatitel, metabeskrivning, URL-slug och alt-texter. Du granskar och redigerar artikeln och skickar den sedan till WordPress som **utkast**.

## Status och plan

| Etapp | Innehåll | Status |
|---|---|---|
| 1 | Gränssnitt, förhandsgranskning, redigering, SEO-kontroll, export | ✅ |
| 2 | AI-genererad artikel utifrån sökintention (Claude) | ✅ |
| 3 | **Artikelkö och granskning:** databas, CSV-import, ordning, generera alla, Godkänn/Begär ändringar/Avvisa, historik, varning för liknande sökord | ✅ |
| 4 | Bilder: 4 AI-bilder → komprimerad WebP med beskrivande filnamn, generera om text eller bilder separat | Nästa |
| 5 | WordPress: anslutning, kontroll av SEO-plugin och API-fält, mediauppladdning, utkast med Custom HTML-block, CSS via litet tilläggsplugin, kontroll mot redan publicerade artiklar, internlänkförslag, testläge | Planerat |
| 6 | Schemaläggning: startdatum, klockslag (Europe/Stockholm), ett godkänt inlägg per dag, `status=future` i WordPress, kalender, ändra datum, pausa, återkalla före publicering, skydd mot dubbelpublicering, kontroll att WordPress verkligen publicerat | Planerat |
| 3b | **Ny design och mobilanpassning:** skandinaviskt ljust tema, mobile-first, granskningsvy för mobil, godkännande med bekräftelse | ✅ |
| 7 | Drift: säker inloggning med sessioner, HTTPS, körning på server, säkerhetskopior, driftsättningsguide | Planerat |

## Granskningsflödet

| Status | Betyder | Vad du kan göra |
|---|---|---|
| Utkast | Planerat sökord, eller en artikel där du begärt ändringar | Generera, redigera sökord, redigera texten, skicka till granskning, avvisa |
| Genereras | AI:n skriver på servern (fortsätter även om webbläsaren stängs) | Vänta |
| Väntar på granskning | Klar för din granskning | **Godkänn artikel**, **Begär ändringar**, **Avvisa**, redigera, generera om |
| Godkänd | Du har godkänt exakt den här versionen | Återkalla godkännande, begär ändringar, avvisa |
| Schemalagd / Publicerad | Kommer i etapp 6 | |
| Fel | Genereringen misslyckades; felet visas | Försök igen |
| Avvisad | Arkiverad, kan inte godkännas | Återställ |

Regler som servern upprätthåller, oavsett vad som skickas från webbläsaren:
- Bara artiklar som väntar på granskning kan godkännas.
- Om en godkänd artikel ändras upphävs godkännandet, och den måste godkännas igen.
- Varje godkännande sparas med en kontrollsumma av innehållet. Schemaläggningen i etapp 6 kommer bara att godta innehåll som matchar ett godkännande.
- Alla beslut loggas permanent i tabellen `handelser` och visas under Historik.
- Databasen ligger i `data/studio.db` (SQLite) och är undantagen från git.

### CSV-format

En artikel per rad. Första kolumnen är huvudsökordet, och resten är sekundära sökord, antingen i egna kolumner eller i samma cell åtskilda med komma eller `|`. Semikolon (svensk Excel), komma och tabb fungerar som avgränsare. En rubrikrad är valfri. Huvudsökord som redan finns i kön hoppas över.

```
Huvudsökord;Sekundära sökord
värmepump i gammalt hus;luft-vatten, kostnad
fukt i källare;dränering, lukt
```

## Tester

```bash
pip install -r requirements-dev.txt
playwright install chromium            # en gång, för webbläsartesterna
python -m pytest -q                    # alla tester (enhet + webbläsare)
python -m pytest tests/e2e -q          # bara webbläsartesterna
E2E_SKARMBILDER=skarmbilder python -m pytest tests/e2e -q   # sparar skärmbilder
```

Webbläsartesterna kör hela granskningsflödet på fyra skärmstorlekar: iPhone (390 px), Android (412 px), surfplatta (820 px) och dator (1440 px). De kontrollerar bland annat följande:
- att sidan aldrig går att scrolla i sidled
- att granskningsknapparna syns utan att man scrollar på mobil
- att godkännande kräver kryssrutan
- att formulärfälten är minst 16 px, så att iOS inte zoomar in

## Design

- **Palett:** varm off-white `#F7F5F0`, mörkgrönt `#193D2D`, ljusgrönt `#DCE9DF` och vitt. Ett ljust tema; det mörka temat är borttaget.
- **Typografi:** rubriker i Fraunces (serif) och gränssnitt i Inter.
- **Mobile-first:** grundstilen är byggd för mobil, med brytpunkter vid 720 px (surfplatta) och 1080 px (dator).
- **Mobil:**
  - Navigeringen ligger längst ned.
  - Granskningsknapparna (Godkänn, Begär ändringar, Avvisa) ligger fast längst ned i editorn.
  - Flikarna följer med när du scrollar.
  - Dialoger öppnas som ark nerifrån.
  - Förhandsvisningen växer med artikeln, så att du läser den som en vanlig webbsida.
- **Godkännande:** öppnar alltid en sammanfattning med en kryssruta som måste bockas i. Servern avvisar godkännanden som saknar bekräftelsen.

## Testa på mobilen

**I datorns webbläsare:** öppna http://localhost:8000. I Chrome trycker du `Cmd+Option+I` och sedan `Cmd+Shift+M` och väljer t.ex. iPhone 14. I Safari väljer du *Utvecklare → Gå in i responsivt designläge*.

**På din riktiga telefon (samma wifi som datorn):**

```bash
STUDIO_USER=moa STUDIO_PASSWORD='välj-ett-långt-lösenord' ./scripts/mobiltest.sh
```

Skriptet skriver ut en adress, till exempel `http://192.168.1.23:8000`. Öppna den i telefonens webbläsare och logga in. Skriptet startar inte utan lösenord, eftersom alla på nätverket annars kan nå studion. macOS kan fråga om Python får ta emot inkommande anslutningar; svara Tillåt. Det här är bara till för test i hemnätverket. Publik drift kräver HTTPS och beskrivs i en kommande driftsättningsguide.

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
  mobiltest.sh         Startar studion för test från mobilen på samma wifi
  git-hooks/pre-commit Stoppar commits med nycklar
app/
  main.py          FastAPI-server och API
  config.py        Läser inställningar och hemligheter från .env
  ai_writer.py     Prompt och anrop till Claude
  db.py            SQLite: artikelkö och granskningslogg
  jobb.py          Generering i bakgrunden (max 2 samtidigt)
  planering.py     CSV-import och kontroll av liknande sökord
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
