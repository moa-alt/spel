"""Serverinställningar. Hemligheter läses från miljön/.env och lämnar aldrig servern."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@dataclass(frozen=True)
class Settings:
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    openai_api_key: str = os.getenv("OPENAI_API_KEY", "")
    wp_url: str = os.getenv("WP_URL", "").rstrip("/")
    wp_user: str = os.getenv("WP_USER", "")
    wp_app_password: str = os.getenv("WP_APP_PASSWORD", "")
    studio_user: str = os.getenv("STUDIO_USER", "")
    studio_password: str = os.getenv("STUDIO_PASSWORD", "")

    def status(self) -> dict:
        """Vilka integrationer som är konfigurerade – bara ja/nej, aldrig själva värdena."""
        return {
            "ai_text": bool(self.anthropic_api_key),
            "bilder": bool(self.openai_api_key),
            "wordpress": bool(self.wp_url and self.wp_user and self.wp_app_password),
            "inloggning": bool(self.studio_user and self.studio_password),
        }


settings = Settings()
