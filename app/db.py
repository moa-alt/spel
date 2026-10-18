"""Permanent lagring av artikelkön i SQLite.

Databasen ligger i data/studio.db (eller STUDIO_DB) och innehåller inga hemligheter.
"""

import hashlib
import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DATA_DIR = Path(os.getenv("STUDIO_DATA", Path(__file__).resolve().parent.parent / "data"))
DB_PATH = Path(os.getenv("STUDIO_DB", DATA_DIR / "studio.db"))

# Statusar i den ordning en artikel normalt går igenom dem, plus fel och avvisad.
STATUSAR = ("utkast", "genereras", "granskas", "godkand", "schemalagd", "publicerad", "fel", "avvisad")

SCHEMA = """
CREATE TABLE IF NOT EXISTS artiklar (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    position      INTEGER NOT NULL,
    huvudsokord   TEXT    NOT NULL,
    sekundara     TEXT    NOT NULL DEFAULT '[]',
    status        TEXT    NOT NULL DEFAULT 'utkast',
    innehall      TEXT,
    fel           TEXT,
    andringar     TEXT,
    godkand_at    TEXT,
    godkand_hash  TEXT,
    wp_post_id    INTEGER,
    publiceras_at TEXT,
    skapad        TEXT    NOT NULL,
    uppdaterad    TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS artiklar_position ON artiklar(position);

-- Permanent logg över granskningsbeslut och andra viktiga händelser.
CREATE TABLE IF NOT EXISTS handelser (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    artikel_id  INTEGER NOT NULL,
    handling    TEXT    NOT NULL,
    kommentar   TEXT,
    innehall_hash TEXT,
    tid         TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS handelser_artikel ON handelser(artikel_id);
"""

_lock = threading.Lock()


def nu() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _conn():
    # Ett skrivlås räcker gott för en studio med en användare; SQLite sköter resten.
    with _lock:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()


def init() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _conn() as c:
        c.execute("PRAGMA journal_mode = WAL")
        c.executescript(SCHEMA)
        # Generering som pågick när servern stängdes av kan inte återupptas.
        c.execute(
            "UPDATE artiklar SET status = 'fel', fel = ?, uppdaterad = ? WHERE status = 'genereras'",
            ("Genereringen avbröts när servern startades om. Försök igen.", nu()),
        )


def lagg_till_kolumn(c, tabell: str, kolumn: str, definition: str) -> None:
    """Enkel migrering: lägger till en kolumn i en befintlig databas om den saknas."""
    if kolumn not in {r[1] for r in c.execute(f"PRAGMA table_info({tabell})")}:
        c.execute(f"ALTER TABLE {tabell} ADD COLUMN {kolumn} {definition}")


def _rad(r: sqlite3.Row, med_innehall: bool) -> dict:
    d = {
        "id": r["id"],
        "position": r["position"],
        "huvudsokord": r["huvudsokord"],
        "sekundara_sokord": json.loads(r["sekundara"]),
        "status": r["status"],
        "fel": r["fel"],
        "andringar": r["andringar"],
        "godkand_at": r["godkand_at"],
        "godkand_hash": r["godkand_hash"],
        "wp_post_id": r["wp_post_id"],
        "publiceras_at": r["publiceras_at"],
        "mal_slug": r["mal_slug"] if "mal_slug" in r.keys() else None,
        "skapad": r["skapad"],
        "uppdaterad": r["uppdaterad"],
        "har_innehall": r["innehall"] is not None,
    }
    if med_innehall:
        d["innehall"] = json.loads(r["innehall"]) if r["innehall"] else None
    else:
        innehall = json.loads(r["innehall"]) if r["innehall"] else {}
        d["titel"] = innehall.get("titel")
    return d


def innehall_hash(innehall: dict | None) -> str | None:
    """Kontrollsumma av innehållet, så att ett godkännande gäller exakt den version som granskades."""
    if innehall is None:
        return None
    return hashlib.sha256(json.dumps(innehall, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def logga(artikel_id: int, handling: str, kommentar: str | None = None, hash_: str | None = None) -> None:
    with _conn() as c:
        c.execute(
            "INSERT INTO handelser (artikel_id, handling, kommentar, innehall_hash, tid) VALUES (?, ?, ?, ?, ?)",
            (artikel_id, handling, kommentar, hash_, nu()),
        )


def historik(artikel_id: int) -> list[dict]:
    with _conn() as c:
        rows = c.execute(
            "SELECT handling, kommentar, tid FROM handelser WHERE artikel_id = ? ORDER BY id DESC", (artikel_id,)
        ).fetchall()
    return [dict(r) for r in rows]


PUBLICERINGSHANDELSER = (
    "schemalagd", "omschemalagd", "avbokad", "publicerad", "publicering_simulerad", "wp_utkast_skapat",
    "publicering_stoppad", "publicering_misslyckad", "publicering_forsok", "publicering_missad",
    "publicering_pausad", "publicering_aterupptagen",
)


def publiceringslogg(antal: int = 100) -> list[dict]:
    platser = ",".join("?" for _ in PUBLICERINGSHANDELSER)
    with _conn() as c:
        rows = c.execute(
            f"""SELECT h.artikel_id, h.handling, h.kommentar, h.tid, a.huvudsokord
                FROM handelser h LEFT JOIN artiklar a ON a.id = h.artikel_id
                WHERE h.handling IN ({platser}) ORDER BY h.id DESC LIMIT ?""",
            (*PUBLICERINGSHANDELSER, antal),
        ).fetchall()
    return [dict(r) for r in rows]


def lista() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM artiklar ORDER BY position, id").fetchall()
    return [_rad(r, med_innehall=False) for r in rows]


def hamta(artikel_id: int) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM artiklar WHERE id = ?", (artikel_id,)).fetchone()
    return _rad(r, med_innehall=True) if r else None


def skapa_manga(poster: list[tuple[str, list[str]]]) -> list[int]:
    tid = nu()
    with _conn() as c:
        start = c.execute("SELECT COALESCE(MAX(position), 0) FROM artiklar").fetchone()[0]
        ids = []
        for i, (huvud, sek) in enumerate(poster, start=1):
            cur = c.execute(
                "INSERT INTO artiklar (position, huvudsokord, sekundara, skapad, uppdaterad) VALUES (?, ?, ?, ?, ?)",
                (start + i, huvud, json.dumps(sek, ensure_ascii=False), tid, tid),
            )
            ids.append(cur.lastrowid)
    return ids


def skapa(huvudsokord: str, sekundara: list[str]) -> int:
    return skapa_manga([(huvudsokord, sekundara)])[0]


def uppdatera(artikel_id: int, **falt) -> None:
    """Uppdaterar valda kolumner. innehall och sekundara serialiseras till JSON."""
    if "sekundara" in falt:
        falt["sekundara"] = json.dumps(falt["sekundara"], ensure_ascii=False)
    if "innehall" in falt and falt["innehall"] is not None:
        falt["innehall"] = json.dumps(falt["innehall"], ensure_ascii=False)
    falt["uppdaterad"] = nu()
    kolumner = ", ".join(f"{k} = ?" for k in falt)
    with _conn() as c:
        c.execute(f"UPDATE artiklar SET {kolumner} WHERE id = ?", (*falt.values(), artikel_id))


def byt_status(artikel_id: int, fran: tuple[str, ...], till: str, **falt) -> bool:
    """Byter status bara om artikeln har en av de förväntade statusarna (skydd mot dubbelklick och kapplöpning)."""
    falt.update(status=till, uppdaterad=nu())
    if "innehall" in falt and falt["innehall"] is not None:
        falt["innehall"] = json.dumps(falt["innehall"], ensure_ascii=False)
    kolumner = ", ".join(f"{k} = ?" for k in falt)
    platser = ", ".join("?" for _ in fran)
    with _conn() as c:
        cur = c.execute(
            f"UPDATE artiklar SET {kolumner} WHERE id = ? AND status IN ({platser})",
            (*falt.values(), artikel_id, *fran),
        )
        return cur.rowcount == 1


def ta_bort(artikel_id: int) -> None:
    with _conn() as c:
        c.execute("DELETE FROM artiklar WHERE id = ?", (artikel_id,))


def satt_ordning(ids: list[int]) -> None:
    """Sätter positionen efter listans ordning. Artiklar som inte finns i listan hamnar sist."""
    with _conn() as c:
        alla = [r[0] for r in c.execute("SELECT id FROM artiklar ORDER BY position, id")]
        kanda = [i for i in ids if i in set(alla)]
        ordning = kanda + [i for i in alla if i not in set(kanda)]
        for pos, artikel_id in enumerate(ordning, start=1):
            c.execute("UPDATE artiklar SET position = ? WHERE id = ?", (pos, artikel_id))
