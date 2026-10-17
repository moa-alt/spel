"""Serverinställningar. Hemligheter lämnar aldrig servern.

En hemlighet, till exempel ANTHROPIC_API_KEY, hämtas i den här ordningen:
1. Miljövariabeln, eller .env i projektmappen.
2. En fil som miljövariabeln <NAMN>_FILE pekar på (Docker- eller systemd-secrets).
3. Filen ~/.config/klimathemmet-studio/<NAMN>, som scripts/spara_hemlighet.py skapar.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

HEMLIGHETS_MAPP = Path(os.getenv("STUDIO_SECRETS_DIR", Path.home() / ".config" / "klimathemmet-studio"))


def hemlighet(namn: str) -> str:
    if varde := os.getenv(namn, "").strip():
        return varde
    sokvag = os.getenv(f"{namn}_FILE") or HEMLIGHETS_MAPP / namn
    try:
        return Path(sokvag).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


EFFORT_NIVAER = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = field(default_factory=lambda: hemlighet("ANTHROPIC_API_KEY"), repr=False)
    claude_model: str = field(default_factory=lambda: os.getenv("STUDIO_CLAUDE_MODEL", "claude-opus-5-5"))
    claude_effort: str = field(default_factory=lambda: os.getenv("STUDIO_CLAUDE_EFFORT", "high"))
    openai_api_key: str = field(default_factory=lambda: hemlighet("OPENAI_API_KEY"), repr=False)
    wp_url: str = field(default_factory=lambda: os.getenv("WP_URL", "").rstrip("/"))
    wp_user: str = field(default_factory=lambda: os.getenv("WP_USER", ""))
    wp_app_password: str = field(default_factory=lambda: hemlighet("WP_APP_PASSWORD"), repr=False)
    studio_user: str = field(default_factory=lambda: os.getenv("STUDIO_USER", ""))
    studio_password: str = field(default_factory=lambda: hemlighet("STUDIO_PASSWORD"), repr=False)

    def __post_init__(self):
        if self.claude_effort not in EFFORT_NIVAER:
            raise ValueError(f"STUDIO_CLAUDE_EFFORT måste vara en av: {', '.join(EFFORT_NIVAER)}")

    def status(self) -> dict:
        """Vilka integrationer som är konfigurerade – bara ja/nej, aldrig själva värdena."""
        return {
            "ai_text": bool(self.anthropic_api_key),
            "bilder": bool(self.openai_api_key),
            "wordpress": bool(self.wp_url and self.wp_user and self.wp_app_password),
            "inloggning": bool(self.studio_user and self.studio_password),
        }


settings = Settings()
