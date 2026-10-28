"""Hjälpskript för GitHub Actions: förbereder Fly.io, kontrollerar före och efter driftsättning.

Används av .github/workflows/fly-forbered.yml och fly-driftsatt.yml. Skriver aldrig ut några hemliga värden,
bara namn. Kräver flyctl och miljövariabeln FLY_API_TOKEN (sätts från GitHub Secrets).

    python scripts/fly_github.py appnamn     # appens namn (FLY_APP eller fly.toml)
    python scripts/fly_github.py forbered    # skapa app och volym om de saknas, lägg in runtime-hemligheter
    python scripts/fly_github.py fore        # kontroller före driftsättning
    python scripts/fly_github.py efter       # kontroller av den driftsatta appen
"""

import json
import os
import secrets as slump
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent
VOLYM = "studio_data"
# Hemligheter som appen läser vid körning. Bara de som är satta i GitHub skickas till Fly.io.
RUNTIME = ("STUDIO_USER", "STUDIO_PASSWORD", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "WP_USER", "WP_APP_PASSWORD")
KRAVDA = ("STUDIO_USER", "STUDIO_PASSWORD", "STUDIO_SESSION_SECRET")
# Får aldrig vara en hemlighet: publiceringsläget styrs bara i fly.toml, där det syns i git.
FORBJUDNA = ("STUDIO_PUBLICERING", "STUDIO_MILJO", "STUDIO_BILDTJANST")


class Fel(Exception):
    pass


def fly_toml() -> dict:
    return tomllib.loads((ROT / "fly.toml").read_text(encoding="utf-8"))


def appnamn() -> str:
    return os.getenv("FLY_APP", "").strip() or fly_toml()["app"]


def kor(args: list[str], indata: str | None = None) -> str:
    """Kör flyctl. Indata (hemligheter) skickas via stdin och syns aldrig i processlistan eller loggen."""
    r = subprocess.run(["flyctl", *args], input=indata, capture_output=True, text=True)
    if r.returncode != 0:
        raise Fel(f"flyctl {args[0]} {args[1] if len(args) > 1 else ''} misslyckades: {r.stderr.strip()[-500:]}")
    return r.stdout


def kor_json(args: list[str]) -> list:
    data = json.loads(kor([*args, "--json"]) or "[]")
    return data if isinstance(data, list) else []


def _namn(rad: dict) -> str:
    return rad.get("Name") or rad.get("name") or ""


def finns_app(app: str) -> bool:
    return any(_namn(a) == app for a in kor_json(["apps", "list"]))


def volymer(app: str) -> list[dict]:
    return [v for v in kor_json(["volumes", "list", "-a", app]) if _namn(v) == VOLYM and (v.get("state") or v.get("State")) != "destroyed"]


def hemligheter(app: str) -> set[str]:
    return {_namn(s) for s in kor_json(["secrets", "list", "-a", app])}


def maskiner(app: str) -> list[dict]:
    return kor_json(["machine", "list", "-a", app])


# ---------- Förbered ----------

def forbered(org: str) -> None:
    app = appnamn()
    region = fly_toml().get("primary_region", "arn")
    if finns_app(app):
        print(f"✓ Appen {app} finns redan")
    else:
        kor(["apps", "create", app, "--org", org])
        print(f"✓ Appen {app} skapades i organisationen {org} (ingen maskin startad)")

    if volymer(app):
        print(f"✓ Volymen {VOLYM} finns redan")
    else:
        bas = ["volumes", "create", VOLYM, "--region", region, "--size", "1", "--yes", "-a", app]
        try:
            kor([*bas, "--snapshot-retention", "14"])
            print(f"✓ Volymen {VOLYM} (1 GB, {region}) skapades med ögonblicksbilder i 14 dagar")
        except Fel as e:
            if "snapshot-retention" not in str(e):
                raise
            kor(bas)
            print(f"✓ Volymen {VOLYM} (1 GB, {region}) skapades med Flys standardlagring av ögonblicksbilder")

    befintliga = hemligheter(app)
    for namn in FORBJUDNA:
        if namn in befintliga:
            kor(["secrets", "unset", namn, "--stage", "-a", app])
            print(f"✓ {namn} togs bort som hemlighet; värdet i fly.toml gäller (simulering)")

    nya: dict[str, str] = {}
    for namn in RUNTIME:
        varde = os.getenv(namn, "").strip()
        if varde:
            nya[namn] = varde
    if "STUDIO_PASSWORD" in nya and len(nya["STUDIO_PASSWORD"]) < 12:
        raise Fel("STUDIO_PASSWORD måste vara minst 12 tecken. Ändra det i GitHub Secrets och kör igen.")
    if "STUDIO_SESSION_SECRET" not in befintliga:
        nya["STUDIO_SESSION_SECRET"] = slump.token_urlsafe(48)
        if os.getenv("GITHUB_ACTIONS") == "true":
            # Den nya nyckeln är ingen GitHub Secret; be GitHub maskera den om den skulle hamna i loggen.
            print(f"::add-mask::{nya['STUDIO_SESSION_SECRET']}")
    if nya:
        rader = "".join(f"{k}={v}\n" for k, v in nya.items())
        kor(["secrets", "import", "--stage", "-a", app], indata=rader)
        print(f"✓ Hemligheter sparade i Fly.io (används från nästa driftsättning): {', '.join(sorted(nya))}")
    saknas = [n for n in KRAVDA if n not in befintliga | set(nya)]
    if saknas:
        raise Fel(f"Saknas fortfarande: {', '.join(saknas)}. Lägg in dem som GitHub Secrets och kör igen.")
    print("Klart. Inget har driftsatts. Starta driftsättningen med workflowen ”Driftsätt till Fly.io”.")


# ---------- Före och efter driftsättning ----------

def fore() -> None:
    app = appnamn()
    sys.path.insert(0, str(ROT / "scripts"))
    import kontrollera_drift

    fel = [t for ok, t in kontrollera_drift.kontroller() if not ok]
    if fel:
        raise Fel("Konfigurationen är inte redo: " + "; ".join(fel))
    if fly_toml()["env"].get("STUDIO_PUBLICERING") != "simulering":
        raise Fel("STUDIO_PUBLICERING i fly.toml är inte simulering. Driftsättningen stoppas.")
    if not finns_app(app):
        raise Fel(f"Appen {app} finns inte på Fly.io. Kör först workflowen ”Förbered Fly.io”.")
    if not volymer(app):
        raise Fel(f"Volymen {VOLYM} saknas. Kör först workflowen ”Förbered Fly.io”.")
    befintliga = hemligheter(app)
    if saknas := [n for n in KRAVDA if n not in befintliga]:
        raise Fel(f"Hemligheter saknas på Fly.io: {', '.join(saknas)}. Kör ”Förbered Fly.io”.")
    if forbjudna := [n for n in FORBJUDNA if n in befintliga]:
        raise Fel(f"{', '.join(forbjudna)} får inte vara hemligheter. Kör ”Förbered Fly.io”, som tar bort dem.")
    if len(maskiner(app)) > 1:
        raise Fel("Appen har fler än en maskin. Studion ska köra på exakt en.")
    print(f"✓ Redo att driftsätta {app}: simulering, volym {VOLYM}, inloggning och sessionsnyckel finns.")


def _hamta(url: str) -> tuple[int, str, str]:
    class IngenOmdirigering(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None

    oppnare = urllib.request.build_opener(IngenOmdirigering)
    try:
        with oppnare.open(urllib.request.Request(url, headers={"User-Agent": "studio-kontroll"}), timeout=15) as r:
            return r.status, r.read(2000).decode("utf-8", "ignore"), r.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.code, "", e.headers.get("Location", "")


def efter(forsok: int = 30, vanta: float = 5.0) -> None:
    app = appnamn()
    bas = f"https://{app}.fly.dev"
    for i in range(forsok):
        try:
            if _hamta(f"{bas}/healthz")[0] == 200:
                break
        except (urllib.error.URLError, TimeoutError, OSError):
            pass
        time.sleep(vanta)
    else:
        raise Fel(f"{bas}/healthz svarade inte. Se loggarna med Fly.io:s Monitoring-sida.")
    print(f"✓ {bas} svarar över HTTPS")
    kod = _hamta(f"{bas}/api/artiklar")[0]
    if kod != 401:
        raise Fel(f"/api/artiklar svarade {kod} utan inloggning; förväntat 401.")
    print("✓ Inloggning krävs (401 utan inloggning)")
    kod, _, plats = _hamta(f"http://{app}.fly.dev/login")
    if kod not in (301, 302, 307, 308) or not plats.startswith("https://"):
        raise Fel(f"HTTP leder inte vidare till HTTPS (svar {kod}).")
    print("✓ HTTP leds vidare till HTTPS")
    m = maskiner(app)
    if len(m) != 1:
        raise Fel(f"Appen har {len(m)} maskiner; den ska ha exakt en.")
    print("✓ Exakt en maskin kör (en schemaläggare)")
    loggar = ""
    for _ in range(6):
        loggar = kor(["logs", "--no-tail", "-a", app])
        if "publiceringsläge" in loggar:
            break
        time.sleep(5)
    if "publiceringsläge simulering" not in loggar:
        raise Fel("Hittade inte raden ”publiceringsläge simulering” i loggen. Kontrollera loggarna innan du använder studion.")
    print("✓ Loggen bekräftar publiceringsläge simulering")


def main(argv: list[str]) -> int:
    try:
        if argv == ["appnamn"]:
            print(appnamn())
        elif argv[:1] == ["forbered"]:
            forbered(argv[1] if len(argv) > 1 else "personal")
        elif argv == ["fore"]:
            fore()
        elif argv == ["efter"]:
            efter()
        else:
            print(__doc__)
            return 2
    except Fel as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
