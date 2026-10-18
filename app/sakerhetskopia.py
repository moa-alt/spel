"""Fullständig säkerhetskopia (databas och bilder) och återställning.

Skapa ett arkiv:   python -m app.sakerhetskopia arkiv /tmp/studio-komplett.tgz
Återställa:        lägg arkivet som <STUDIO_DATA>/aterstall.tgz och starta om appen.

Återställningen görs innan servern startar, så att databasen aldrig byts medan den används.
Nuvarande data flyttas först till <STUDIO_DATA>/fore-aterstallning-<tid>/, så att inget går förlorat.
Arkivet får bara innehålla studio.db, manifest.json och bilder/<artikel-id>/<fil>.webp.
"""

import io
import json
import logging
import re
import shutil
import sqlite3
import sys
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import config, db

log = logging.getLogger("studio.sakerhetskopia")

ATERSTALL = "aterstall.tgz"
TILLATNA = re.compile(r"^(studio\.db|manifest\.json|bilder/\d+/[1-9]\d?-[0-9a-f]{12}\.webp)$")
MAX_STORLEK = 2 * 1024 * 1024 * 1024


def skapa_arkiv(mal: Path) -> dict:
    """Konsistent kopia av databasen och alla bildfiler i ett tar.gz-arkiv."""
    with tempfile.TemporaryDirectory() as tmp:
        kopia = Path(tmp) / "studio.db"
        with db._conn() as c:
            dest = sqlite3.connect(kopia)
            try:
                c.backup(dest)
            finally:
                dest.close()
        mapp = config.settings.data_dir / "bilder"
        filer = sorted(p for p in mapp.rglob("*.webp") if p.is_file()) if mapp.is_dir() else []
        manifest = {"skapad": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bilder": len(filer),
                    "databas_bytes": kopia.stat().st_size}
        with tarfile.open(mal, "w:gz") as tar:
            tar.add(kopia, arcname="studio.db")
            for f in filer:
                tar.add(f, arcname=f"bilder/{f.parent.name}/{f.name}")
            data = json.dumps(manifest, indent=2).encode()
            info = tarfile.TarInfo("manifest.json")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return manifest


def _kontrollera(tar: tarfile.TarFile) -> list[tarfile.TarInfo]:
    medlemmar = tar.getmembers()
    namn = set()
    total = 0
    for m in medlemmar:
        if m.isdir():
            continue
        if not m.isfile() or not TILLATNA.match(m.name):
            raise ValueError(f"Otillåten fil i arkivet: {m.name!r}")
        total += m.size
        namn.add(m.name)
    if "studio.db" not in namn:
        raise ValueError("Arkivet saknar studio.db.")
    if total > MAX_STORLEK:
        raise ValueError("Arkivet är för stort.")
    return [m for m in medlemmar if m.isfile()]


def aterstall_vid_start(data_dir: Path | None = None) -> str | None:
    """Återställer från <data>/aterstall.tgz om filen finns. Returnerar en beskrivning, eller None."""
    data_dir = data_dir or config.settings.data_dir
    arkiv = data_dir / ATERSTALL
    if not arkiv.is_file():
        return None
    tid = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    try:
        with tarfile.open(arkiv, "r:gz") as tar:
            medlemmar = _kontrollera(tar)
            with tempfile.TemporaryDirectory(dir=data_dir) as tmp:
                for m in medlemmar:
                    mal = Path(tmp) / m.name
                    mal.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(m) as kalla, open(mal, "wb") as ut:
                        shutil.copyfileobj(kalla, ut)
                # Databasen måste gå att öppna innan något byts ut.
                kontroll = sqlite3.connect(Path(tmp) / "studio.db")
                try:
                    if kontroll.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise ValueError("Databasen i arkivet är skadad.")
                    kontroll.execute("SELECT COUNT(*) FROM artiklar").fetchone()
                    # Schemaläggarlåset i kopian tillhör en instans som inte längre finns; släpp det.
                    try:
                        kontroll.execute("DELETE FROM installningar WHERE nyckel = 'schemalaggare_las'")
                        kontroll.commit()
                    except sqlite3.OperationalError:
                        pass
                finally:
                    kontroll.close()
                spara = data_dir / f"fore-aterstallning-{tid}"
                spara.mkdir()
                med_bilder = (Path(tmp) / "bilder").is_dir()
                # Ett arkiv med bara databasen (t.ex. en nattlig kopia) låter bilderna ligga kvar.
                for namn in ("studio.db", "studio.db-wal", "studio.db-shm") + (("bilder",) if med_bilder else ()):
                    if (data_dir / namn).exists():
                        shutil.move(str(data_dir / namn), spara / namn)
                shutil.move(str(Path(tmp) / "studio.db"), data_dir / "studio.db")
                if med_bilder:
                    shutil.move(str(Path(tmp) / "bilder"), data_dir / "bilder")
        arkiv.rename(data_dir / f"aterstall-klar-{tid}.tgz")
        text = f"Återställd från arkivet. Tidigare data finns i {spara.name}/."
        log.warning(text)
        return text
    except (ValueError, tarfile.TarError, sqlite3.DatabaseError, OSError) as e:
        # Appen startar med sina nuvarande data; det felaktiga arkivet läggs åt sidan.
        arkiv.rename(data_dir / f"aterstall-fel-{tid}.tgz")
        text = f"Återställningen avbröts och inget ändrades: {e}"
        log.error(text)
        return text


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if len(argv) == 2 and argv[0] == "arkiv":
        # Körs bredvid den igång varande appen: databasen läses bara, inget initieras om.
        info = skapa_arkiv(Path(argv[1]))
        print(f"Arkiv skapat: {argv[1]} ({info['bilder']} bilder)")
        return 0
    if argv == ["aterstall-vid-start"]:
        if text := aterstall_vid_start():
            print(text)
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
