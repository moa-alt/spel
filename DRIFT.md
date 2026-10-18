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

**Löpande kostnader utöver hostingen:**
- **Claude (artikeltext):** ungefär 2–4 kr per artikel med standardinställningarna. Det debiteras av Anthropic per användning. Sätt en månadsgräns i Anthropic Console.
- **Bilder:** cirka 1,6 kr per artikel (fyra bilder med `gpt-image-2`, kvalitet medium). Det debiteras av bildtjänsten, och kostnaden visas i studion innan något skapas. Sätt en månadsgräns hos leverantören. Lägg in nyckeln med `scripts/fly_hemlighet.sh OPENAI_API_KEY`.
- **Egen domän** som `studio.klimathemmet.se`: ingen extra kostnad om du redan har domänen. Certifikatet är gratis.

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

## Steg för steg

### 1. Installera Fly-verktyget och logga in

```bash
brew install flyctl
fly auth signup      # eller: fly auth login
```

Lägg in ett betalkort i Fly.io. Sätt gärna en kostnadsvarning under *Billing*.

### 2. Skapa appen, utan att driftsätta

I projektmappen:

```bash
fly launch --no-deploy --copy-config --name klimathemmet-studio-<något-eget>
```

Svara **nej** på frågor om databaser och Redis. Om namnet ändras, uppdatera `app = ...` i `fly.toml`.

### 3. Skapa den permanenta volymen

```bash
fly volumes create studio_data --region arn --size 1
```

### 4. Lägg in hemligheter

Hemligheter som sparas med `fly secrets` lagras krypterade hos Fly.io och syns aldrig i koden, på GitHub eller i loggarna. Kör kommandona i din egen terminal. Klistra aldrig in värdena i en chatt.

```bash
fly secrets set STUDIO_USER=moa
./scripts/fly_hemlighet.sh STUDIO_PASSWORD        # välj ett långt, unikt lösenord, minst 12 tecken
./scripts/fly_hemlighet.sh ANTHROPIC_API_KEY
./scripts/fly_hemlighet.sh OPENAI_API_KEY         # för AI-bilder

# Nyckel som signerar inloggningssessioner (slumpas fram, du behöver inte spara den)
python3 -c 'import secrets; print("STUDIO_SESSION_SECRET=" + secrets.token_urlsafe(48))' | fly secrets import
```

Skriptet visar inte det du skriver och skickar värdet direkt till Fly.io. Värdet hamnar varken i terminalhistoriken eller i processlistan.

### 5. Flytta över dina befintliga artiklar och bilder (valfritt)

Har du redan artiklar i `data/studio.db` och bilder i `data/bilder/` på din Mac, gör så här efter steg 6. Stoppa först studion på din Mac, så att databasen inte ändras under kopieringen.

```bash
tar -czf studio-data.tgz -C data studio.db bilder
fly ssh sftp shell
put studio-data.tgz /data/studio-data.tgz
exit
fly ssh console -C "tar -xzf /data/studio-data.tgz -C /data"
fly ssh console -C "rm /data/studio-data.tgz"
fly apps restart
```

Efter flytten är det serverns kopia som gäller. Arbeta sedan bara i den molnbaserade studion, så att artiklarna inte finns i två versioner.

### 6. Driftsätt, när du har godkänt det

```bash
fly deploy
```

Studion nås då på `https://<appnamn>.fly.dev`, med HTTPS från start. Logga in med användarnamnet och lösenordet från steg 4. Lägg gärna till studion på mobilens hemskärm: i Safari väljer du *Dela → Lägg till på hemskärmen*.

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

Publiceringsläget kan bara ändras på servern, inte från gränssnittet. På så sätt kan ingen slå på publicering av misstag.

| Läge | Vad som händer på publiceringsdagen |
|---|---|
| `simulering` (standard) | Alla kontroller körs och loggas. Inget skickas till WordPress. |
| `utkast` | Ett **utkast** skapas i WordPress. Inget publiceras. Bra för att kontrollera design och SEO-fält på riktigt. |
| `skarp` | Artikeln **publiceras** på klimathemmet.se. |

```bash
fly secrets set STUDIO_PUBLICERING=utkast     # prova först
fly secrets set STUDIO_PUBLICERING=skarp      # först när du är nöjd
```

Ett nytt värde startar om appen, och läget visas överst i *Publicering*.

## Daglig användning

- **Pausa:** slå på *Paus* i Publicering. Inget publiceras förrän du slår av den igen.
- **Ändra datum:** välj ett nytt datum på artikeln under *Kommande*. Upptagna dagar nekas.
- **Stoppa en artikel:** välj *Ta bort* i kalendern. Du kan också återkalla godkännandet, begära ändringar eller redigera texten. Då tas artikeln automatiskt ur kalendern och måste godkännas igen.

## Uppdatera appen

```bash
git pull
python -m pytest -q        # alla tester ska gå igenom
fly deploy
```

Databasen och säkerhetskopiorna ligger på volymen och påverkas inte. Gick något fel tar `fly releases` och `fly deploy --image <tidigare avbild>` dig tillbaka till förra versionen.

## Återställa från säkerhetskopia

```bash
fly ssh console -C "ls /data/backups"
fly ssh console -C "cp /data/backups/studio-ÅÅÅÅ-MM-DD.db /data/studio.db"
fly apps restart
```

Du kan också använda Flys ögonblicksbilder med `fly volumes snapshots list`.

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
