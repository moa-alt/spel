"""Kontrollerar att riktig AI-bildgenerering är redo, utan att skapa någon bild.

Användning:
    python scripts/kontrollera_bilder.py

Visar om .env hittas och ignoreras av git, om OPENAI_API_KEY finns (aldrig själva nyckeln),
vilken bildtjänst och modell som används och vad en bild beräknas kosta. Sedan frågar skriptet
bildtjänsten om modellen finns, vilket är gratis. Ingen bild genereras.
"""

import asyncio
import subprocess
import sys
from pathlib import Path

ROT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROT))

from dotenv import dotenv_values  # noqa: E402

from app import bilder, config  # noqa: E402


def main() -> int:
    s = config.settings
    env = config.ENV_FIL
    print(f".env:               {'hittad' if env.is_file() else 'saknas'} ({env})")
    if env.is_file():
        ignorerad = subprocess.run(["git", "check-ignore", "-q", str(env)], cwd=ROT).returncode == 0
        print(f"Ignoreras av git:   {'ja' if ignorerad else 'NEJ – lägg till .env i .gitignore innan du committar'}")
    if s.openai_api_key:
        kalla = ".env" if (dotenv_values(env).get("OPENAI_API_KEY") or "").strip() else "miljövariabel eller hemlighetsfil"
        print(f"OPENAI_API_KEY:     hittad (från {kalla})")
    else:
        print("OPENAI_API_KEY:     saknas")
    print(f"Bildtjänst:         {s.bildtjanst}" + ("  (testbilder – inga riktiga AI-bilder)" if s.bildtjanst == "test" else ""))
    print(f"Modell / kvalitet:  {s.bild_modell} / {s.bild_kvalitet}")
    k = bilder.kostnad(1)
    print(f"Kostnad per bild:   ca {k['total_usd']} USD (≈ {k['total_sek']} kr), uppskattning")
    print("Kontrollerar nyckel och modell hos bildtjänsten (gratis, ingen bild skapas)…")
    r = asyncio.run(bilder.kontrollera())
    print(("✓ " if r["ok"] else "✗ ") + r["meddelande"])
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
