"""Kontrollerar att projektet är redo för en säker driftsättning på Fly.io. Ändrar ingenting.

Användning:
    python scripts/kontrollera_drift.py

Kontrollerar fly.toml, Dockerfile, .dockerignore, beroenden och git. Skriver aldrig ut några hemligheter.
Avslutas med kod 0 om allt är i ordning, annars 1.
"""

import re
import subprocess
import sys
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent

try:
    import tomllib
except ModuleNotFoundError:  # Python före 3.11
    sys.exit("Kräver Python 3.11 eller senare (tomllib).")

HEMLIGA_NAMN = re.compile(r"KEY|PASSWORD|SECRET|TOKEN", re.I)


def kontroller() -> list[tuple[bool, str]]:
    ut: list[tuple[bool, str]] = []

    def k(villkor, text):
        ut.append((bool(villkor), text))

    fly = tomllib.loads((ROT / "fly.toml").read_text(encoding="utf-8"))
    env = fly.get("env", {})
    http = fly.get("http_service", {})
    k(env.get("STUDIO_PUBLICERING") == "simulering", "Publiceringsläget är simulering (inget skickas till WordPress)")
    k(env.get("STUDIO_MILJO") == "produktion", "Produktionsläge: appen vägrar starta utan inloggning, sessionsnyckel och HTTPS")
    k(env.get("STUDIO_DATA") == "/data", "Data sparas i /data")
    mounts = fly.get("mounts", {})
    mounts = mounts[0] if isinstance(mounts, list) else mounts
    k(mounts.get("destination") == "/data" and mounts.get("source"), "Permanent volym monteras på /data")
    k(http.get("force_https") is True, "HTTPS tvingas")
    k(http.get("auto_stop_machines") in ("off", False), "Maskinen stängs inte av vid inaktivitet")
    k((http.get("min_machines_running") or 0) >= 1, "Minst en maskin är alltid igång")
    k(any(c.get("path") == "/healthz" for c in http.get("checks", [])), "Hälsokontroll på /healthz")
    k(not [n for n in env if HEMLIGA_NAMN.search(n)], "Inga hemligheter i fly.toml (de läggs in med fly secrets)")

    docker = (ROT / "Dockerfile").read_text(encoding="utf-8")
    k(not re.search(r"^COPY\s+\.\s", docker, re.M), "Dockerfile kopierar bara appen, inte hela projektmappen")
    k("useradd" in docker, "Appen körs som en vanlig användare, inte root")
    ignorerat = (ROT / ".dockerignore").read_text(encoding="utf-8").split()
    k(".env" in ignorerat and "data" in ignorerat, ".env och data/ skickas inte till Flys byggserver")

    krav = (ROT / "requirements.txt").read_text(encoding="utf-8").lower()
    k(all(p in krav for p in ("fastapi", "uvicorn", "pillow", "anthropic", "tzdata")), "Alla beroenden finns i requirements.txt")

    try:
        spårade = subprocess.run(["git", "ls-files"], cwd=ROT, capture_output=True, text=True, check=True).stdout.split()
        k(".env" not in spårade, ".env finns inte i git")
        k(subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=ROT).returncode == 0, ".env ignoreras av git")
    except (OSError, subprocess.CalledProcessError):
        k(False, "Kunde inte läsa git")
    return ut


def main() -> int:
    fly = tomllib.loads((ROT / "fly.toml").read_text(encoding="utf-8"))
    print(f"Appnamn i fly.toml: {fly.get('app')}  (region {fly.get('primary_region')})\n")
    resultat = kontroller()
    for ok, text in resultat:
        print(("✓ " if ok else "✗ ") + text)
    fel = [t for ok, t in resultat if not ok]
    print("\nRedo för driftsättning." if not fel else f"\n{len(fel)} punkt(er) att åtgärda före driftsättning.")
    return 0 if not fel else 1


if __name__ == "__main__":
    sys.exit(main())
