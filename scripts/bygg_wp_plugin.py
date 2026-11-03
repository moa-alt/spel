"""Bygger tilläggspluginet som en zip-fil att ladda upp i WordPress.

Användning:  python scripts/bygg_wp_plugin.py
Resultat:    dist/klimathemmet-studio.zip  (Tillägg → Lägg till nytt → Ladda upp tillägg)

Artiklarnas CSS kopieras från app/static/artikel.css (äldre artiklar) och app/static/design.css
(designsystemet), så att sajten och studion alltid har samma design.
"""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KALLA = ROOT / "wordpress-plugin" / "klimathemmet-studio"
CSS = ROOT / "app" / "static" / "artikel.css"
DESIGN_CSS = ROOT / "app" / "static" / "design.css"


def bygg(mal: Path = ROOT / "dist" / "klimathemmet-studio.zip") -> Path:
    mal.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(mal, "w", zipfile.ZIP_DEFLATED) as z:
        for fil in sorted(KALLA.iterdir()):
            z.write(fil, f"klimathemmet-studio/{fil.name}")
        z.write(CSS, "klimathemmet-studio/artikel.css")
        z.write(DESIGN_CSS, "klimathemmet-studio/design.css")
    return mal


if __name__ == "__main__":
    print(f"Byggt: {bygg().relative_to(ROOT)}")
