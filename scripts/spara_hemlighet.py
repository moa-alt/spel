"""Sparar en API-nyckel eller ett lösenord utanför projektet, utan att det syns på skärmen.

Användning:
    python scripts/spara_hemlighet.py                      # ANTHROPIC_API_KEY
    python scripts/spara_hemlighet.py WP_APP_PASSWORD

Värdet skrivs till ~/.config/klimathemmet-studio/<NAMN> med rättigheter så att
bara ditt användarkonto kan läsa det. Det hamnar inte i projektmappen, git,
terminalhistoriken eller loggar.
"""

import getpass
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import HEMLIGHETS_MAPP  # noqa: E402

TILLATNA = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY", "WP_APP_PASSWORD", "STUDIO_PASSWORD"}


def testa_anthropic(nyckel: str) -> None:
    import anthropic

    try:
        anthropic.Anthropic(api_key=nyckel).models.retrieve("claude-opus-5-5")
        print("✓ Nyckeln fungerar.")
    except anthropic.AuthenticationError:
        print("✗ Nyckeln godkändes inte. Kontrollera att du kopierade hela nyckeln.")
    except anthropic.APIConnectionError:
        print("! Kunde inte nå Anthropic för att testa nyckeln. Den är sparad ändå.")
    except anthropic.APIStatusError as e:
        print(f"! Nyckeln är sparad, men testet gav statuskod {e.status_code}.")


def main() -> None:
    namn = sys.argv[1] if len(sys.argv) > 1 else "ANTHROPIC_API_KEY"
    if namn not in TILLATNA:
        sys.exit(f"Okänt namn. Välj ett av: {', '.join(sorted(TILLATNA))}")

    varde = getpass.getpass(f"Klistra in {namn} (inget visas när du klistrar in) och tryck Enter: ").strip()
    if not varde:
        sys.exit("Inget sparades.")
    if namn == "ANTHROPIC_API_KEY" and not varde.startswith("sk-ant-"):
        sys.exit("Det där ser inte ut som en Anthropic-nyckel (den ska börja med sk-ant-). Inget sparades.")

    HEMLIGHETS_MAPP.mkdir(parents=True, exist_ok=True)
    os.chmod(HEMLIGHETS_MAPP, 0o700)
    fil = HEMLIGHETS_MAPP / namn
    tmp = fil.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(varde + "\n")
    os.replace(tmp, fil)
    print(f"Sparat i {fil} (endast läsbar för ditt konto).")

    if namn == "ANTHROPIC_API_KEY":
        testa_anthropic(varde)
    if namn == "OPENAI_API_KEY":
        print("Testa nyckeln utan kostnad med: python scripts/kontrollera_bilder.py")
    print("Starta om servern så att den läser in värdet.")


if __name__ == "__main__":
    main()
