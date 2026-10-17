"""Serverinställningar. Hemligheter läses från miljön/.env och lämnar aldrig servern."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


EFFORT_NIVAER = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("STUDIO_CLAUDE_MODEL", "claude-opus-5-5")
    claude_effort: str = os.getenv("STUDIO_CLAUDE_EFFORT", "high")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    wp_url: str = os.getenv("WP_URL", "").rstrip("/")
    wp_user: str = os.getenv("WP_USER", "")
    wp_app_password: str = os.getenv("WP_APP_PASSWORD", "")
    studio_user: str = os.getenv("STUDIO_USER", "")
    studio_password: str = os.getenv("STUDIO_PASSWORD", "")

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
