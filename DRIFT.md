# Driftsättning av Klimathemmet AI Studio

Den här guiden beskriver hur studion körs dygnet runt på en server, så att du kan använda den från mobilen och datorn och så att publiceringen fungerar även när din Mac är avstängd.

> **Inget i den här guiden har körts.** Varje steg utförs av dig, i den ordning du väljer. Automatisk publicering på klimathemmet.se förblir avstängd (`STUDIO_PUBLICERING=simulering`) tills du själv ändrar den i steg 9.

## Rekommendation: Fly.io i Stockholm

| | **Fly.io** (rekommenderas) | **Render** |
|---|---|---|
| Ungefärlig kostnad/månad | **ca 3,5–4 USD** (≈ 40 kr) | ca 7,25 USD (≈ 80 kr) |
| Vad som ingår | 1 delad CPU, 512 MB, 1 GB permanent volym, HTTPS | 0,5 CPU, 512 MB, 1 GB permanent disk, HTTPS |
| Serverplats | Stockholm (`arn`) | Frankfurt |
| FastAPI, bakgrundsjobb, SQLite och bilder | Ja: en maskin som alltid är igång med en monterad volym | Ja: Starter-tjänst med monterad disk |
| HTTPS, certifikat och operativsystem | Sköts åt dig | Sköts åt dig |
| Säkerhetskopior av disk | Dagliga ögonblicksbilder av volymen | Dagliga ögonblicksbilder av disken |
| Driftsättning | `fly deploy` från din Mac | Automatiskt från GitHub |

**Rekommendation: Fly.io.** Det är billigast och har en server i Stockholm. Appen (`Dockerfile`, `fly.toml`) är redan förberedd för Fly.io, och allt utom själva driftsättningen sköts från terminalen. Render är lika enkelt men ungefär dubbelt så dyrt. En egen VPS (till exempel Hetzner, ca 6 EUR/mån) ger mer kraft för pengarna, men då sköter du själv säkerhetsuppdateringar och HTTPS, vilket inte lönar sig för ett mindre affiliateprojekt.

Priserna kommer från leverantörernas prissidor och prisjämförelser i oktober 2026 och kan ändras. Kontrollera alltid aktuella priser innan du startar. Fly.io: shared-cpu-1x 512 MB ≈ 3,3 USD/mån plus volym 0,15 USD per GB och månad. Render: Starter 7 USD plus disk 0,25 USD per GB och månad. Hetzner höjde priserna flera gånger under 2026.

### Månadskostnad för den faktiska konfigurationen

Konfigurationen i `fly.toml`: en maskin `shared-cpu-1x` med 512 MB som alltid är igång i Stockholm, en volym på 1 GB och ögonblicksbilder som sparas i 14 dagar.

| Resurs som debiteras | Pris | Per månad |
|---|---|---|
| Maskin `shared-cpu-1x`, 512 MB, igång dygnet runt | ca 3,69 USD/mån | **ca 3,69 USD** |
| Volym `studio_data`, 1 GB (debiteras för hela storleken, även när den inte är full) | 0,15 USD/GB | 0,15 USD |
| Ögonblicksbilder av volymen (14 dagar; databas och bilder är långt under 10 GB) | första 10 GB gratis, sedan 0,08 USD/GB | 0 USD |
| Delad IPv4 och IPv6 | gratis | 0 USD |
| HTTPS-certifikat för `<appnamn>.fly.dev` | gratis | 0 USD |
| Utgående trafik (en person som använder studion och uppladdning till WordPress) | några öre per GB | < 0,10 USD |
| Swap på 512 MB (använder maskinens disk) | ingår | 0 USD |
| **Totalt** | | **ca 3,9 USD (≈ 40 kr)** |

Undvik det här, eftersom det kostar extra: en dedikerad IPv4-adress (`fly ips allocate-v4`, 2 USD/mån), fler maskiner och större volymer än du behöver.

Priserna kommer från Fly.io:s prislista i oktober 2026 och kan ändras. Fly.io höjde priset på minne den 1 oktober 2026. Sätt en kostnadsvarning under *Billing* i Fly.io.

**Utöver hostingen tillkommer:**
- **Claude (artikeltext):** ungefär 2–4 kr per artikel. Det debiteras av Anthropic per användning. Sätt en månadsgräns i Anthropic Console.
- **Bilder:** cirka 1,6 kr per artikel (fyra bilder med `gpt-image-2`, kvalitet medium). Kostnaden visas i studion innan något skapas. Sätt en månadsgräns hos OpenAI.
- **Egen domän** som `studio.klimathemmet.se`: ingen extra kostnad om du redan har domänen.

## Hur driften fungerar

- **En maskin:** appen körs som en enda process på en maskin som alltid är igång (`auto_stop_machines = "off"`). Schemaläggaren finns i samma process och kontrollerar publiceringskön var 30:e sekund.
- **Databas:** SQLite i `/data/studio.db`, på en permanent volym som finns kvar vid omstarter och uppdateringar.
- **Bilder:** WebP-filer i `/data/bilder/`, på samma volym. En artikels fyra bilder tar cirka 1 MB, så volymen på 1 GB räcker till flera hundra artiklar. Utöka med `fly volumes extend` vid behov.
- **Publicering:** sker på det datum och klockslag du valt i kalendern, i svensk tid (Europe/Stockholm, med sommartid).
  - Precis före publicering kontrolleras att texten och bilderna är exakt de du godkände.
  - Kvalitetskontrollen körs igen. Har en länkad sida försvunnit sedan godkännandet stoppas publiceringen. Länkar till godkända framtida mål som ännu inte är publicerade blir vanlig text.
  - Bilderna laddas upp till mediabiblioteket, huvudbilden blir utvald bild, och kategori, slug, publiceringsdatum, SEO-titel, metabeskrivning och fokusnyckelord skickas med. WordPress-id och adress sparas.
  - Tillfälliga fel (timeout, serverfel, för många anrop) försöks igen efter 5 och 10 minuter. Fel som inte går över av sig själva, som saknad behörighet eller ogiltiga fält, stoppar direkt med ett tydligt meddelande.
- **En schemaläggare åt gången:** varje instans måste ta ett lås i databasen innan den publicerar. Låset förnyas var 30:e sekund och går ut efter två minuter om instansen slutar svara. Varje publicering markeras dessutom som pågående i ett enda databassteg, så att två processer aldrig kan ta samma jobb.
- **Skydd mot dubbelpublicering:**
  - Databasen tillåter högst en publicering per artikel och per dag.
  - I WordPress återanvänds ett inlägg som redan är märkt med artikelns id.
  - En bild laddas aldrig upp två gånger.
  - Om servern startas om mitt i en publicering återupptas den, och redan skapade inlägg återanvänds, så att inget publiceras dubbelt.
  - En dag som passerar medan servern är nere eller publiceringen pausad publiceras inte i efterhand. Artikeln visas som *Missad* och schemaläggs på nytt av dig.
- **Säkerhetskopior:**
  - Appen sparar en kopia av databasen varje natt i `/data/backups` och behåller 14.
  - Fly.io tar dessutom dagliga ögonblicksbilder av hela volymen, inklusive bilderna i `/data/bilder`. De nattliga databaskopiorna innehåller inte bildfilerna.
  - Du kan ladda ner en kopia när som helst i *Publicering → Säkerhetskopia*.
- **Loggar:**
  - Publiceringsloggen i studion visar varje schemaläggning, publicering, paus och varje fel.
  - Serverns tekniska loggar ser du med `fly logs`. Där finns WordPress-fel med statuskod, återförsök och vilken instans som håller schemaläggarlåset.
  - Inga nycklar eller lösenord loggas.

## Driftsättning via GitHub Actions (rekommenderat, utan Fly CLI)

Allt görs i webbläsaren, på fly.io och github.com. Repot har tre workflows:

| Workflow | Startas | Vad den gör |
|---|---|---|
| **Tester** | Automatiskt vid varje push och pull request | Kör alla tester och kontrollerar driftkonfigurationen. Driftsätter ingenting och använder inga hemligheter. |
| **Förbered Fly.io** | Bara manuellt, med ordet `FORBERED` | Skapar appen och volymen om de saknas och lägger in runtime-hemligheterna på Fly.io. Startar ingen maskin. |
| **Driftsätt till Fly.io** | Bara manuellt, med ordet `DRIFTSATT` | Kör alla tester, kontrollerar säkerheten, driftsätter en maskin och kontrollerar resultatet. |

**Spärrar i driftsättningen.** Workflowen stoppar om något av följande inte stämmer:
- Publiceringsläget i `fly.toml` är `simulering`.
- `STUDIO_PUBLICERING` finns inte som hemlighet hos Fly.io.
- Volymen finns.
- Inloggning och sessionsnyckel är inlagda.
- Det finns högst en maskin.

**Kontroller efter driftsättningen:**
- `/healthz` svarar över HTTPS.
- Sidor kräver inloggning (svar 401).
- HTTP leds vidare till HTTPS.
- Exakt en maskin kör.
- Loggen visar `publiceringsläge simulering`.

**Hemligheter.**
- `FLY_API_TOKEN` och inloggningsuppgifterna sparas som GitHub Secrets.
- Workflowen *Förbered Fly.io* skickar runtime-hemligheterna vidare till Fly.io via stdin och skriver bara ut deras namn.
- GitHub maskerar alla secrets i loggarna. Repot är offentligt, så loggarna kan läsas av alla, men de innehåller inga hemliga värden.
- Sessionsnyckeln slumpas fram i workflowen, maskeras och skickas direkt till Fly.io.

### Steg 1 – Fly.io: konto och token (gratis)

1. Skapa ett konto på https://fly.io och slå på tvåstegsverifiering.
2. Lägg in ett betalkort under *Billing*. Fly.io kräver det innan appar och volymer kan skapas.
3. Skapa en token som GitHub får använda:
   1. Öppna https://fly.io/dashboard och välj organisationen **personal**.
   2. Öppna **Tokens** och skapa en ny token med namnet `github-actions` och en begränsad giltighetstid, till exempel ett år.
   3. Kopiera token direkt; den visas bara en gång. Klistra aldrig in den i en chatt.

   Hittar du inte *Tokens* under organisationen finns motsvarande under ditt konto, *Account → Access Tokens*. Token behöver kunna skapa appar och volymer, eftersom *Förbered Fly.io* gör det.

### Steg 2 – GitHub: secrets och miljö

Gå till https://github.com/moa-alt/spel → **Settings → Secrets and variables → Actions**.

**Fliken *Secrets* → *New repository secret*, en i taget:**

| Namn | Värde | Krävs |
|---|---|---|
| `FLY_API_TOKEN` | Token från steg 1 | Ja |
| `STUDIO_USER` | Ditt användarnamn till studion, t.ex. `moa` | Ja |
| `STUDIO_PASSWORD` | Ett långt, unikt lösenord, minst 12 tecken | Ja |
| `OPENAI_API_KEY` | Din OpenAI-nyckel | För bilder |
| `ANTHROPIC_API_KEY` | Din Anthropic-nyckel | För AI-text |
| `WP_USER`, `WP_APP_PASSWORD` | WordPress-användare och applikationslösenord | Nej, vänta gärna |

`STUDIO_SESSION_SECRET` behöver du inte skapa; den slumpas fram automatiskt. WordPress-uppgifterna används inte så länge läget är `simulering`.

**Fliken *Variables* (bara om appnamnet `klimathemmet-studio` är upptaget hos Fly.io):** *New repository variable*, namn `FLY_APP` och värde till exempel `klimathemmet-studio-moa`.

**Rekommenderat: kräv ditt eget godkännande före varje körning.**
1. Gå till **Settings → Environments → New environment**, skriv namnet `fly-produktion` och klicka *Configure environment*.
2. Bocka i **Required reviewers**, lägg till dig själv (`moa-alt`) och klicka *Save protection rules*.
3. Under *Deployment branches and tags*, välj *Selected branches and tags* och lägg till `claude/sweet-allen-aid5yk`.

Då väntar både förberedelsen och driftsättningen på att du klickar *Approve and deploy*.

### Steg 3 – Kör *Förbered Fly.io* (skapar betalda resurser)

Från och med det här steget debiteras volymen, cirka 0,15 USD/mån. Ingen maskin startas.

1. Gå till **Actions** → **Förbered Fly.io** → **Run workflow**.
2. Välj grenen `claude/sweet-allen-aid5yk`, skriv `FORBERED` i rutan och låt organisationen vara `personal`.
3. Klicka **Run workflow**. Godkänn körningen om du har slagit på *Required reviewers*.
4. Kontrollera i loggen att raderna med ✓ visar appen, volymen och namnen på hemligheterna, aldrig deras värden.

När körningen är klar ligger runtime-hemligheterna på Fly.io. Du kan då ta bort `STUDIO_PASSWORD`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY` och WordPress-uppgifterna från GitHub, så att de bara finns på ett ställe. Behåll `FLY_API_TOKEN` och `STUDIO_USER`. En ny körning lägger bara in de secrets som finns kvar i GitHub.

### Steg 4 – Kör *Driftsätt till Fly.io* (startar maskinen)

Från och med det här steget debiteras maskinen, cirka 3,7 USD/mån.

1. Gå till **Actions** → **Driftsätt till Fly.io** → **Run workflow**.
2. Välj grenen `claude/sweet-allen-aid5yk`, skriv `DRIFTSATT` och klicka **Run workflow**.
3. Först körs alla tester, cirka 5–10 minuter. Därefter körs kontrollerna, driftsättningen och kontrollerna efteråt. Godkänn steget om du har slagit på *Required reviewers*.
4. Öppna `https://klimathemmet-studio.fly.dev` (eller ditt eget appnamn) och logga in med `STUDIO_USER` och `STUDIO_PASSWORD`.

### Senare uppdateringar

Varje push kör *Tester* automatiskt, men ingenting driftsätts. Du driftsätter en ny version genom att köra *Driftsätt till Fly.io* igen. Databasen, bilderna och kalendern ligger kvar på volymen.

Säkerhetskopiering och återställning beskrivs längre ner. Kommandona där kräver Fly CLI. Den kompletta kopian kan du alltid ladda ner med knappen i *Publicering → Säkerhetskopia*.

## Alternativ: steg för steg med Fly CLI på din Mac

Kör ett kommando i taget i Terminal på din Mac, i projektmappen. Steg 2 och framåt skapar resurser hos Fly.io; kör dem först när du har godkänt driftsättningen.

### 0. Kontrollera projektet (ändrar ingenting)

```bash
git pull
python -m pytest -q
python scripts/kontrollera_drift.py
```

Alla tester ska gå igenom, och kontrollskriptet ska avsluta med *Redo för driftsättning*.

### 1. Installera Fly-verktyget och logga in

```bash
brew install flyctl
fly auth login
```

Saknar du Homebrew: `curl -L https://fly.io/install.sh | sh`. Lägg sedan in ett betalkort i Fly.io och slå på tvåstegsverifiering för ditt konto.

### 2. Skapa appen, utan att driftsätta

Vi använder `fly apps create` i stället för `fly launch`, eftersom `fly.toml` redan är färdig och inte ska skrivas om.

```bash
fly apps create klimathemmet-studio
```

Är namnet upptaget, välj ett annat, till exempel `klimathemmet-studio-moa`. Ändra då raden `app = ...` i `fly.toml` till samma namn.

### 3. Skapa den permanenta volymen

```bash
fly volumes create studio_data --region arn --size 1 --snapshot-retention 14
```

Fly varnar för att en volym bara finns på en server och föreslår två volymer. Svara **y**: studion ska köra på exakt en maskin, och säkerheten kommer från ögonblicksbilderna och de nattliga kopiorna.

### 4. Lägg in hemligheter, innan första driftsättningen

Hemligheter som sparas med `fly secrets` lagras krypterade hos Fly.io och syns aldrig i koden, på GitHub eller i loggarna. Kör kommandona i din egen terminal och klistra aldrig in värdena i en chatt. Utan de tre första vägrar appen att starta, så studion blir aldrig åtkomlig utan inloggning.

```bash
fly secrets set STUDIO_USER=moa --stage
```
```bash
./scripts/fly_hemlighet.sh STUDIO_PASSWORD
```
```bash
python3 -c 'import secrets; print("STUDIO_SESSION_SECRET=" + secrets.token_urlsafe(48))' | fly secrets import --stage
```
```bash
./scripts/fly_hemlighet.sh ANTHROPIC_API_KEY
```
```bash
./scripts/fly_hemlighet.sh OPENAI_API_KEY
```

- `STUDIO_PASSWORD`: välj ett långt, unikt lösenord på minst 12 tecken.
- `STUDIO_SESSION_SECRET` slumpas fram; du behöver inte spara den.
- Kontrollera med `fly secrets list`. Den visar bara namnen, aldrig värdena.
- WordPress-uppgifterna läggs in först i steg 8. Publiceringsläget är inte en hemlighet; det står i `fly.toml` och är `simulering`.

Skriptet `fly_hemlighet.sh` visar inte det du skriver och skickar värdet direkt till Fly.io. Värdet hamnar varken i terminalhistoriken eller i processlistan.

### 5. Flytta över dina befintliga artiklar och bilder (valfritt, efter steg 6)

Har du artiklar och bilder i studion på din Mac kan du flytta dem till servern. Stoppa först studion på din Mac (Ctrl+C), så att inget ändras under tiden. Kör sedan, ett kommando i taget:

```bash
python -m app.sakerhetskopia arkiv studio-komplett.tgz
```
```bash
fly ssh sftp shell
```

Skriv i sftp-skalet `put studio-komplett.tgz /data/aterstall.tgz` och sedan `exit`.

```bash
fly apps restart
```

Appen återställer arkivet innan den startar (se *Återställa från säkerhetskopia*). Efter flytten är det serverns kopia som gäller. Arbeta sedan bara i den molnbaserade studion, så att artiklarna inte finns i två versioner.

### 6. Driftsätt, när du har godkänt det

```bash
fly deploy --ha=false
```

`--ha=false` ger exakt en maskin. Kontrollera sedan, ett kommando i taget:

```bash
fly status
```
```bash
fly logs
```
```bash
fly open
```

- `fly status`: en maskin i `arn`, status *started*.
- `fly logs`: raderna `Start: miljö produktion, publiceringsläge simulering, inloggning på` och `Schemaläggaren startad`. Avsluta med Ctrl+C.
- `fly open` öppnar `https://<appnamn>.fly.dev`, med HTTPS från start. Logga in med användarnamnet och lösenordet från steg 4.

Lägg gärna till studion på mobilens hemskärm: i Safari väljer du *Dela → Lägg till på hemskärmen*.

### 7. Egen adress (valfritt)

```bash
fly certs add studio.klimathemmet.se
```

Lägg in den CNAME-post som kommandot visar hos din domänleverantör. Certifikatet skapas automatiskt.

### 8. Koppla WordPress, fortfarande utan publicering

1. **Installera tilläggspluginet.** Kör `python scripts/bygg_wp_plugin.py` och ladda upp `dist/klimathemmet-studio.zip` under *Tillägg → Lägg till nytt → Ladda upp tillägg* i WordPress, och aktivera det. Pluginet gör tre saker:
   - Det laddar artikeldesignen som en vanlig CSS-fil, så att ingen `<style>`-kod behöver ligga i inläggen.
   - Det gör SEO-fälten för Yoast eller Rank Math skrivbara via REST API, bara för användare som får redigera inlägget.
   - Det märker inlägg som studion skapat, så att samma artikel aldrig publiceras två gånger.
2. **Skapa ett applikationslösenord.** Gå till *Användare → Profil → Applikationslösenord* och använd gärna en egen användare med rollen Redaktör. Lägg sedan in uppgifterna:
   ```bash
   fly secrets set WP_USER=ai-studio
   ./scripts/fly_hemlighet.sh WP_APP_PASSWORD
   ```
3. **Kontrollera anslutningen.** I studion trycker du på *Publicering → Kontrollera anslutning*. Kontrollen ändrar ingenting på sajten. Den visar
   - om inloggningen fungerar och användaren får publicera
   - vilket SEO-plugin sajten har
   - om pluginets fält går att skriva till

### 9. Slå på publiceringen i tre steg, när du är redo

Publiceringsläget kan bara ändras i `fly.toml` och en ny driftsättning, inte från gränssnittet. På så sätt kan ingen slå på publicering av misstag.

| Läge | Vad som händer på publiceringsdagen |
|---|---|
| `simulering` (standard) | Alla kontroller körs och loggas. Inget skickas till WordPress. |
| `utkast` | Ett **utkast** skapas i WordPress. Inget publiceras. Bra för att kontrollera design och SEO-fält på riktigt. |
| `skarp` | Artikeln **publiceras** på klimathemmet.se. |

```bash
# Ändra i fly.toml: STUDIO_PUBLICERING = "utkast"   (prova först)
fly deploy --ha=false
# Först när du är nöjd och har godkänt det: STUDIO_PUBLICERING = "skarp"
fly deploy --ha=false
```

Läget sätts bara i `fly.toml`, aldrig som hemlighet, så att det alltid syns i git vilket läge som gäller. Det visas överst i *Publicering* och i loggen vid start.

## Daglig användning

- **Pausa:** slå på *Paus* i Publicering. Inget publiceras förrän du slår av den igen.
- **Ändra datum:** välj ett nytt datum på artikeln under *Kommande*. Upptagna dagar nekas.
- **Stoppa en artikel:** välj *Ta bort* i kalendern. Du kan också återkalla godkännandet, begära ändringar eller redigera texten. Då tas artikeln automatiskt ur kalendern och måste godkännas igen.

## Uppdatera appen

```bash
git pull
python -m pytest -q
python scripts/kontrollera_drift.py
fly deploy --ha=false
```

Databasen, bilderna och kalendern ligger på volymen och påverkas inte. Maskinen startas om med den nya versionen, och schemaläggaren fortsätter där den var. Gick något fel tar `fly releases` och `fly deploy --image <tidigare avbild>` dig tillbaka till förra versionen.

## Säkerhetskopiering

Det finns tre lager:

| Vad | Var | Hur länge |
|---|---|---|
| Databasen, varje natt | `/data/backups/studio-ÅÅÅÅ-MM-DD.db` på volymen | 14 dagar |
| Hela volymen (databas, bilder, nattliga kopior) | Fly.io:s ögonblicksbilder, dagligen | 14 dagar (`--snapshot-retention 14`) |
| Komplett kopia (databas och bilder) | Din Mac, när du laddar ner den | Så länge du vill |

Ladda ner en komplett kopia till din Mac ibland, till exempel en gång i veckan. Antingen med knappen i *Publicering → Säkerhetskopia → Ladda ner komplett kopia*, eller från terminalen:

```bash
fly ssh console -C "python -m app.sakerhetskopia arkiv /data/kopia.tgz"
```
```bash
fly ssh sftp get /data/kopia.tgz studio-komplett.tgz
```
```bash
fly ssh console -C "rm /data/kopia.tgz"
```

## Återställa från säkerhetskopia

Återställningen görs alltid när appen startar, aldrig medan databasen används:
1. Du lägger arkivet som `/data/aterstall.tgz` och startar om.
2. Appen kontrollerar arkivet: bara databas och bilder är tillåtna, och databasen måste vara hel.
3. Nuvarande data flyttas till `/data/fore-aterstallning-<tid>/`, så att inget går förlorat.
4. Är arkivet felaktigt ändras ingenting och appen startar som vanligt.

**Från en komplett kopia på din Mac:**

```bash
fly ssh sftp shell
```

Skriv i sftp-skalet `put studio-komplett.tgz /data/aterstall.tgz` och sedan `exit`. Starta därefter om:

```bash
fly apps restart
```

Kontrollera med `fly logs` att raden `Återställd från arkivet` finns.

**Från en nattlig kopia (bara databasen; bilderna ligger kvar):**

```bash
fly ssh console -C "sh -c 'cd /tmp && cp /data/backups/studio-ÅÅÅÅ-MM-DD.db studio.db && tar -czf /data/aterstall.tgz studio.db'"
```
```bash
fly apps restart
```

**Om hela volymen skulle gå förlorad:** `fly volumes snapshots list <volym-id>` visar ögonblicksbilderna. Skapa en ny volym från en av dem med `fly volumes create studio_data --region arn --snapshot-id <id>`, och kör sedan `fly deploy --ha=false`.

## Innan du godkänner driftsättning

Gör de här kontrollerna på din Mac. Inget av det ändrar klimathemmet.se.

1. `git pull` och `python -m pytest -q`: alla tester ska gå igenom. De använder bara låtsas-WordPress och låtsasbilder.
2. Starta studion lokalt (`uvicorn app.main:app --reload`) och gå igenom en artikel hela vägen:
   1. Generera artikeln och skapa en bild.
   2. Granska kvalitetskontrollen och välj kategori.
   3. Godkänn artikeln och bocka av faktapåståendena.
   4. Schemalägg den i *Publicering*, flytta den och avbryt.
3. Kontrollera att `.env` inte finns i git: `git status --ignored | grep .env` ska visa den som ignorerad, och `git log --all -- .env` ska inte visa något.
4. Säg till när du vill driftsätta. Följ sedan stegen ovan i ordning. Publiceringen står kvar på `simulering` tills du själv byter läge i steg 9.

## Säkerhetschecklista

- [ ] Starkt, unikt lösenord för studion, och tvåstegsverifiering på ditt Fly.io-konto
- [ ] Alla hemligheter är inlagda med `fly secrets set`, aldrig i `fly.toml`, koden eller GitHub
- [ ] WordPress-användaren för studion har rollen Redaktör, inte Administratör
- [ ] Månadsgränser satta hos Anthropic, OpenAI och Fly.io
- [ ] Bara en maskin kör appen (`fly scale count 1`); volymen kan bara monteras på en maskin åt gången
- [ ] `STUDIO_PUBLICERING` står på `simulering` tills du uttryckligen vill publicera

Det här skyddar studion:
- **Om säkerhetsinställningar saknas** vägrar appen starta i produktionsläge (`STUDIO_MILJO=produktion`).
- **Inloggning:**
  - Sessionerna är signerade och gäller i 14 dagar.
  - Sessionskakan är Secure, HttpOnly och SameSite=Strict.
  - Efter 5 misslyckade inloggningsförsök spärras IP-adressen i 15 minuter.
- **Förfalskade anrop:** alla ändrande anrop kräver en säkerhetsrubrik (skydd mot CSRF).
- **Webbläsaren:**
  - HTTPS tvingas, och webbläsaren instrueras att alltid använda HTTPS (HSTS).
  - En strikt innehållspolicy (CSP) hindrar sidan från att köra kod från andra källor.
  - Sökmotorer instrueras att inte indexera studion.
