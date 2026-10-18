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

# .env läses in om inte STUDIO_DOTENV=0 (testerna stänger av den, så att de aldrig får dina riktiga inställningar).
ENV_FIL = Path(os.getenv("STUDIO_ENV_FIL", Path(__file__).resolve().parent.parent / ".env"))
if os.getenv("STUDIO_DOTENV", "1") != "0":
    load_dotenv(ENV_FIL)

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
# simulering: inget skickas till WordPress. utkast: skapar utkast i WordPress. skarp: publicerar.
PUBLICERINGSLAGEN = ("simulering", "utkast", "skarp")
# openai: OpenAI:s bild-API (eller en kompatibel tjänst via STUDIO_BILD_API_URL). test: lokala låtsasbilder
# utan kostnad, bara för tester och utveckling. av: bildgenerering avstängd.
BILDTJANSTER = ("openai", "test", "av")
BILDKVALITETER = ("low", "medium", "high")
# Ungefärligt pris i USD per bild i 1536×1024 för standardmodellen, per kvalitet. Kontrollera aktuella priser
# hos leverantören och ange STUDIO_BILD_PRIS_USD om de skiljer sig.
STANDARDPRIS_USD = {"low": 0.005, "medium": 0.041, "high": 0.165}


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
    session_secret: str = field(default_factory=lambda: hemlighet("STUDIO_SESSION_SECRET"), repr=False)
    # "produktion" kräver inloggning, sessionsnyckel och HTTPS.
    miljo: str = field(default_factory=lambda: os.getenv("STUDIO_MILJO", "utveckling"))
    publicering: str = field(default_factory=lambda: os.getenv("STUDIO_PUBLICERING", "simulering"))
    schemalaggare: bool = field(default_factory=lambda: os.getenv("STUDIO_SCHEMALAGGARE", "pa") != "av")
    bildtjanst: str = field(default_factory=lambda: os.getenv("STUDIO_BILDTJANST", "openai"))
    bild_modell: str = field(default_factory=lambda: os.getenv("STUDIO_BILD_MODELL", "gpt-image-2"))
    bild_kvalitet: str = field(default_factory=lambda: os.getenv("STUDIO_BILD_KVALITET", "medium"))
    bild_api_url: str = field(default_factory=lambda: os.getenv("STUDIO_BILD_API_URL", "https://api.openai.com/v1").rstrip("/"))
    bild_pris_usd: float = field(default_factory=lambda: float(os.getenv("STUDIO_BILD_PRIS_USD") or 0))
    bild_timeout: float = field(default_factory=lambda: float(os.getenv("STUDIO_BILD_TIMEOUT", "180")))
    usd_sek: float = field(default_factory=lambda: float(os.getenv("STUDIO_USD_SEK", "10")))
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("STUDIO_DATA", Path(__file__).resolve().parent.parent / "data")))

    def __post_init__(self):
        if self.claude_effort not in EFFORT_NIVAER:
            raise ValueError(f"STUDIO_CLAUDE_EFFORT måste vara en av: {', '.join(EFFORT_NIVAER)}")
        if self.publicering not in PUBLICERINGSLAGEN:
            raise ValueError(f"STUDIO_PUBLICERING måste vara en av: {', '.join(PUBLICERINGSLAGEN)}")
        if self.bildtjanst not in BILDTJANSTER:
            raise ValueError(f"STUDIO_BILDTJANST måste vara en av: {', '.join(BILDTJANSTER)}")
        if self.bild_kvalitet not in BILDKVALITETER:
            raise ValueError(f"STUDIO_BILD_KVALITET måste vara en av: {', '.join(BILDKVALITETER)}")
        if not self.bild_pris_usd:
            object.__setattr__(self, "bild_pris_usd", STANDARDPRIS_USD[self.bild_kvalitet])

    @property
    def produktion(self) -> bool:
        return self.miljo == "produktion"

    @property
    def bilder_aktiva(self) -> bool:
        if self.bildtjanst == "test":
            return not self.produktion
        return self.bildtjanst == "openai" and bool(self.openai_api_key)

    @property
    def inloggning_aktiv(self) -> bool:
        return bool(self.studio_user and self.studio_password)

    def kontrollera_produktion(self) -> None:
        """Stoppar uppstarten i produktion om säkerhetsinställningar saknas."""
        if not self.produktion:
            return
        problem = []
        if not self.inloggning_aktiv:
            problem.append("STUDIO_USER och STUDIO_PASSWORD måste vara satta")
        elif len(self.studio_password) < 12:
            problem.append("STUDIO_PASSWORD måste vara minst 12 tecken")
        if len(self.session_secret) < 32:
            problem.append("STUDIO_SESSION_SECRET måste vara minst 32 tecken")
        if self.bildtjanst == "test":
            problem.append("STUDIO_BILDTJANST=test är bara till för tester och kan inte användas i produktion")
        if self.wp_url and not self.wp_url.startswith("https://"):
            problem.append("WP_URL måste börja med https://")
        if problem:
            raise RuntimeError("Produktionsläge kan inte starta: " + "; ".join(problem))

    def status(self) -> dict:
        """Vilka integrationer som är konfigurerade – bara ja/nej, aldrig själva värdena."""
        return {
            "ai_text": bool(self.anthropic_api_key),
            "bilder": self.bilder_aktiva,
            "wordpress": bool(self.wp_url and self.wp_user and self.wp_app_password),
            "inloggning": self.inloggning_aktiv,
            "publicering": self.publicering,
        }


settings = Settings()
