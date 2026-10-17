# Driftsättning av Klimathemmet AI Studio

Den här guiden beskriver hur studion körs dygnet runt på en server, så att du kan använda den från mobilen och datorn och så att publiceringen fungerar även när din Mac är avstängd.

> **Inget i den här guiden har körts.** Varje steg utförs av dig, i den ordning du väljer. Automatisk publicering på klimathemmet.se förblir avstängd (`STUDIO_PUBLICERING=simulering`) tills du själv ändrar den i steg 9.

## Rekommendation: Fly.io i Stockholm

| | Fly.io (rekommenderas) | Render | Egen VPS (Hetzner) |
|---|---|---|---|
| Ungefärlig kostnad/månad | **ca 3,5–4 USD** (≈ 40 kr) | ca 7,25 USD (≈ 80 kr) | ca 5,5–6 EUR (≈ 65 kr) |
| Vad som ingår | 1 delad CPU, 512 MB, 1 GB volym, HTTPS | 0,5 CPU, 512 MB, 1 GB disk, HTTPS | 2 CPU, 4 GB, 40 GB disk |
| Serverplats | Stockholm (`arn`) | Frankfurt | Tyskland/Finland |
| HTTPS och certifikat | Automatiskt | Automatiskt | Du sätter upp själv (t.ex. Caddy) |
| Underhåll av operativsystem | Sköts åt dig | Sköts åt dig | Du ansvarar själv |
| Säkerhetskopior av disk | Dagliga ögonblicksbilder av volymen | Dagliga ögonblicksbilder av disken | Tillval, ca 20 % av priset |

Fly.io är billigast bland alternativen där någon annan sköter servern. Det har en server i Stockholm och ger HTTPS automatiskt, och appen behöver inga ändringar för att köras där. Render är lika enkelt men ungefär dubbelt så dyrt. En egen VPS ger mest kraft för pengarna men kräver att du själv sköter säkerhetsuppdateringar.

Priserna kommer från leverantörernas prissidor och prisjämförelser i oktober 2026 och kan ändras. Kontrollera alltid aktuella priser innan du startar. Fly.io: shared-cpu-1x 512 MB ≈ 3,3 USD/mån plus volym 0,15 USD per GB och månad. Render: Starter 7 USD plus disk 0,25 USD per GB och månad. Hetzner höjde priserna flera gånger under 2026.

**Löpande kostnader utöver hostingen:**
- **Claude (artikeltext):** ungefär 2–4 kr per artikel med standardinställningarna. Det debiteras av Anthropic per användning. Sätt en månadsgräns i Anthropic Console.
- **Bilder:** tillkommer när bildgenereringen byggs.
- **Egen domän** som `studio.klimathemmet.se`: ingen extra kostnad om du redan har domänen. Certifikatet är gratis.

## Hur driften fungerar

- **En maskin:** appen körs som en enda process på en maskin som alltid är igång (`auto_stop_machines = "off"`). Schemaläggaren finns i samma process och kontrollerar publiceringskön var 30:e sekund.
- **Databas:** SQLite i `/data/studio.db`, på en permanent volym som finns kvar vid omstarter och uppdateringar.
- **Publicering:** sker på utsatt tid, svensk tid (Europe/Stockholm, med sommartid).
  - Precis före publicering kontrolleras att texten är exakt den du godkände.
  - Om servern startas om mitt i en publicering återupptas den, och redan skapade inlägg återanvänds, så att inget publiceras dubbelt.
  - En dag som passerar medan servern är nere eller publiceringen pausad publiceras inte i efterhand. Artikeln visas som *Missad* och schemaläggs på nytt av dig.
- **Säkerhetskopior:**
  - Appen sparar en kopia av databasen varje natt i `/data/backups` och behåller 14.
  - Fly.io tar dessutom dagliga ögonblicksbilder av hela volymen.
  - Du kan ladda ner en kopia när som helst i *Publicering → Säkerhetskopia*.
- **Loggar:**
  - Publiceringsloggen i studion visar varje schemaläggning, publicering, paus och varje fel.
  - Serverns tekniska loggar ser du med `fly logs`.
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

# Nyckel som signerar inloggningssessioner (slumpas fram, du behöver inte spara den)
python3 -c 'import secrets; print("STUDIO_SESSION_SECRET=" + secrets.token_urlsafe(48))' | fly secrets import
```

Skriptet visar inte det du skriver och skickar värdet direkt till Fly.io. Värdet hamnar varken i terminalhistoriken eller i processlistan.

### 5. Flytta över dina befintliga artiklar (valfritt)

Har du redan artiklar i `data/studio.db` på din Mac, gör så här efter steg 6:

```bash
fly ssh sftp shell
put data/studio.db /data/studio.db
exit
fly apps restart
```

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

## Säkerhetschecklista

- [ ] Starkt, unikt lösenord för studion, och tvåstegsverifiering på ditt Fly.io-konto
- [ ] Alla hemligheter är inlagda med `fly secrets set`, aldrig i `fly.toml`, koden eller GitHub
- [ ] WordPress-användaren för studion har rollen Redaktör, inte Administratör
- [ ] Månadsgränser satta hos Anthropic och Fly.io
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
