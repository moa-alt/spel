"""Publiceringskö och schemaläggare.

Regler som alltid gäller, oavsett vad gränssnittet skickar:
- Bara godkända artiklar vars innehåll fortfarande matchar godkännandet kan schemaläggas.
- Högst en publicering per kalenderdag (svensk tid), skyddat av ett unikt index i databasen.
- En artikel kan bara publiceras en gång (också ett unikt index).
- Precis före publicering kontrolleras innehållet igen; har det ändrats stoppas publiceringen.
- Pausad publicering skickar ingenting. Missade dagar publiceras inte i efterhand.

Schemaläggaren körs i serverprocessen och kontrollerar kön varje halvminut. Efter en omstart
återupptas avbrutna publiceringar, och WordPress-klienten återanvänder redan skapade inlägg,
så att inget publiceras dubbelt.
"""

import asyncio
import json
import logging
import sqlite3
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from . import config, db
from .wordpress import WordPress, WordPressFel

log = logging.getLogger("studio.publicering")

TZ = ZoneInfo("Europe/Stockholm")
INTERVALL_SEKUNDER = 30
MAX_FORSOK = 3
STANDARD_KLOCKSLAG = time(7, 0)

AKTIVA = ("planerad", "publicerar")
# Statusar som upptar en kalenderdag.
UPPTAR_DAG = ("planerad", "publicerar", "publicerad")

SCHEMA = """
CREATE TABLE IF NOT EXISTS publiceringar (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    artikel_id    INTEGER NOT NULL,
    datum         TEXT    NOT NULL,   -- lokalt datum (Europe/Stockholm), ÅÅÅÅ-MM-DD
    tid_utc       TEXT    NOT NULL,   -- publiceringstid i UTC
    status        TEXT    NOT NULL,   -- planerad, publicerar, publicerad, utkast, simulerad, avbokad, missad, fel
    godkand_hash  TEXT    NOT NULL,
    wp_post_id    INTEGER,
    wp_lank       TEXT,
    forsok        INTEGER NOT NULL DEFAULT 0,
    nasta_forsok  TEXT,
    fel           TEXT,
    skapad        TEXT    NOT NULL,
    uppdaterad    TEXT    NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS publicering_en_per_dag
    ON publiceringar(datum) WHERE status IN ('planerad', 'publicerar', 'publicerad');
CREATE UNIQUE INDEX IF NOT EXISTS publicering_en_per_artikel
    ON publiceringar(artikel_id) WHERE status IN ('planerad', 'publicerar', 'publicerad');
CREATE TABLE IF NOT EXISTS installningar (
    nyckel TEXT PRIMARY KEY,
    varde  TEXT NOT NULL
);
"""


class PubliceringsFel(Exception):
    """Regelbrott med ett meddelande som kan visas för användaren."""


def init() -> None:
    with db._conn() as c:
        c.executescript(SCHEMA)
        # En publicering som avbröts av en omstart görs om; WordPress-steget är idempotent.
        c.execute("UPDATE publiceringar SET status = 'planerad', uppdaterad = ? WHERE status = 'publicerar'", (db.nu(),))


# ---------- Tid ----------

def nu_utc() -> datetime:
    return datetime.now(timezone.utc)


def lokal_tid(datum: date, klockslag: time) -> datetime:
    return datetime.combine(datum, klockslag, tzinfo=TZ)


def till_utc_text(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def fran_utc_text(text: str) -> datetime:
    return datetime.fromisoformat(text)


def idag_lokalt(nu: datetime | None = None) -> date:
    return (nu or nu_utc()).astimezone(TZ).date()


# ---------- Inställningar ----------

def pausad() -> bool:
    with db._conn() as c:
        r = c.execute("SELECT varde FROM installningar WHERE nyckel = 'pausad'").fetchone()
    return bool(r and r[0] == "1")


def satt_paus(paus: bool) -> None:
    with db._conn() as c:
        c.execute(
            "INSERT INTO installningar (nyckel, varde) VALUES ('pausad', ?) "
            "ON CONFLICT(nyckel) DO UPDATE SET varde = excluded.varde",
            ("1" if paus else "0",),
        )
    db.logga(0, "publicering_pausad" if paus else "publicering_aterupptagen")


# ---------- Kalender ----------

def _slot(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["lokal_tid"] = fran_utc_text(r["tid_utc"]).astimezone(TZ).isoformat(timespec="minutes")
    return d


def kalender(fran: date | None = None) -> list[dict]:
    """Alla publiceringar från ett datum, med artikelns rubrik och sökord."""
    fran = fran or idag_lokalt() - timedelta(days=60)
    with db._conn() as c:
        rows = c.execute(
            """SELECT p.*, a.huvudsokord, a.innehall
               FROM publiceringar p JOIN artiklar a ON a.id = p.artikel_id
               WHERE p.datum >= ? AND p.status != 'avbokad' ORDER BY p.datum, p.id""",
            (fran.isoformat(),),
        ).fetchall()
    ut = []
    for r in rows:
        d = _slot(r)
        d["titel"] = json.loads(r["innehall"]).get("titel") if r["innehall"] else None
        d.pop("innehall", None)
        ut.append(d)
    return ut


def _upptagna_dagar(c) -> set[str]:
    platser = ",".join("?" for _ in UPPTAR_DAG)
    return {r[0] for r in c.execute(f"SELECT datum FROM publiceringar WHERE status IN ({platser})", UPPTAR_DAG)}


def _kontrollera_godkand(artikel: dict) -> None:
    if artikel["status"] != "godkand":
        raise PubliceringsFel(f"”{artikel['huvudsokord']}” är inte godkänd och kan inte schemaläggas.")
    if not artikel["godkand_hash"] or db.innehall_hash(artikel["innehall"]) != artikel["godkand_hash"]:
        raise PubliceringsFel(f"”{artikel['huvudsokord']}” har ändrats efter godkännandet och måste granskas igen.")


def schemalagg(artikel_ids: list[int], startdatum: date, klockslag: time = STANDARD_KLOCKSLAG,
               nu: datetime | None = None) -> list[dict]:
    """Lägger godkända artiklar i ordning på första lediga dag från startdatum, en per dag."""
    nu = nu or nu_utc()
    if startdatum < idag_lokalt(nu):
        raise PubliceringsFel("Startdatumet har redan passerat.")
    artiklar = [db.hamta(i) for i in artikel_ids]
    if any(a is None for a in artiklar):
        raise PubliceringsFel("En av artiklarna finns inte längre.")
    for a in artiklar:
        _kontrollera_godkand(a)

    resultat = []
    with db._conn() as c:
        upptagna = _upptagna_dagar(c)
        dag = startdatum
        for a in artiklar:
            # Hoppa över upptagna dagar och tider som redan passerat.
            while dag.isoformat() in upptagna or lokal_tid(dag, klockslag) <= nu + timedelta(minutes=5):
                dag += timedelta(days=1)
            tid = lokal_tid(dag, klockslag)
            tidstext = db.nu()
            cur = c.execute(
                "UPDATE artiklar SET status = 'schemalagd', publiceras_at = ?, uppdaterad = ? "
                "WHERE id = ? AND status = 'godkand' AND godkand_hash = ?",
                (till_utc_text(tid), tidstext, a["id"], a["godkand_hash"]),
            )
            if cur.rowcount != 1:
                raise PubliceringsFel(f"”{a['huvudsokord']}” ändrades samtidigt. Försök igen.")
            c.execute(
                "INSERT INTO publiceringar (artikel_id, datum, tid_utc, status, godkand_hash, skapad, uppdaterad) "
                "VALUES (?, ?, ?, 'planerad', ?, ?, ?)",
                (a["id"], dag.isoformat(), till_utc_text(tid), a["godkand_hash"], tidstext, tidstext),
            )
            upptagna.add(dag.isoformat())
            resultat.append({"artikel_id": a["id"], "datum": dag.isoformat(), "lokal_tid": tid.isoformat(timespec="minutes")})
    for r in resultat:
        db.logga(r["artikel_id"], "schemalagd", f"Publiceras {r['lokal_tid'][:16].replace('T', ' ')}")
    return resultat


def flytta(publicering_id: int, nytt_datum: date, klockslag: time | None = None, nu: datetime | None = None) -> dict:
    nu = nu or nu_utc()
    with db._conn() as c:
        r = c.execute("SELECT * FROM publiceringar WHERE id = ?", (publicering_id,)).fetchone()
        if r is None or r["status"] != "planerad":
            raise PubliceringsFel("Bara planerade publiceringar kan flyttas.")
        klockslag = klockslag or fran_utc_text(r["tid_utc"]).astimezone(TZ).time()
        tid = lokal_tid(nytt_datum, klockslag)
        if tid <= nu + timedelta(minutes=5):
            raise PubliceringsFel("Den nya tiden har redan passerat.")
        if nytt_datum.isoformat() != r["datum"] and nytt_datum.isoformat() in _upptagna_dagar(c):
            raise PubliceringsFel(f"{nytt_datum.isoformat()} har redan en publicering. Välj en annan dag.")
        c.execute(
            "UPDATE publiceringar SET datum = ?, tid_utc = ?, fel = NULL, forsok = 0, nasta_forsok = NULL, uppdaterad = ? WHERE id = ?",
            (nytt_datum.isoformat(), till_utc_text(tid), db.nu(), publicering_id),
        )
        c.execute("UPDATE artiklar SET publiceras_at = ? WHERE id = ?", (till_utc_text(tid), r["artikel_id"]))
    db.logga(r["artikel_id"], "omschemalagd", f"Publiceras {tid.isoformat(timespec='minutes')[:16].replace('T', ' ')}")
    return {"datum": nytt_datum.isoformat(), "lokal_tid": tid.isoformat(timespec="minutes")}


def avboka(artikel_id: int, orsak: str, ny_status: str = "godkand") -> bool:
    """Tar bort artikeln från kalendern. Returnerar False om den inte var schemalagd."""
    with db._conn() as c:
        if c.execute("SELECT 1 FROM publiceringar WHERE artikel_id = ? AND status = 'publicerar'", (artikel_id,)).fetchone():
            raise PubliceringsFel("Artikeln publiceras just nu och kan inte avbokas.")
        cur = c.execute(
            "UPDATE publiceringar SET status = 'avbokad', fel = ?, uppdaterad = ? WHERE artikel_id = ? AND status = 'planerad'",
            (orsak, db.nu(), artikel_id),
        )
        if cur.rowcount == 0:
            return False
        c.execute(
            "UPDATE artiklar SET status = ?, publiceras_at = NULL, uppdaterad = ? WHERE id = ? AND status = 'schemalagd'",
            (ny_status, db.nu(), artikel_id),
        )
    db.logga(artikel_id, "avbokad", orsak)
    return True


# ---------- Schemaläggaren ----------

def _satt_slot(c, slot_id: int, **falt) -> None:
    falt["uppdaterad"] = db.nu()
    c.execute(f"UPDATE publiceringar SET {', '.join(f'{k} = ?' for k in falt)} WHERE id = ?", (*falt.values(), slot_id))


def _avsluta(slot: dict, slot_status: str, artikel_status: str, handling: str, kommentar: str, **artikel_falt) -> None:
    with db._conn() as c:
        _satt_slot(c, slot["id"], status=slot_status, fel=kommentar if slot_status in ("fel", "missad", "avbokad") else None,
                   wp_post_id=artikel_falt.get("wp_post_id"), wp_lank=artikel_falt.pop("wp_lank", None))
        falt = {"status": artikel_status, "uppdaterad": db.nu(), **artikel_falt}
        if artikel_status != "publicerad":
            falt["publiceras_at"] = None
        c.execute(f"UPDATE artiklar SET {', '.join(f'{k} = ?' for k in falt)} WHERE id = ?", (*falt.values(), slot["artikel_id"]))
    db.logga(slot["artikel_id"], handling, kommentar)
    log.info("Publicering %s för artikel %s: %s", handling, slot["artikel_id"], kommentar)


async def _publicera(slot: dict, nu: datetime, transport=None) -> None:
    with db._conn() as c:
        cur = c.execute("UPDATE publiceringar SET status = 'publicerar', uppdaterad = ? WHERE id = ? AND status = 'planerad'",
                        (db.nu(), slot["id"]))
        if cur.rowcount != 1:
            return
    artikel = db.hamta(slot["artikel_id"])

    # Sista kontrollen: exakt den godkända versionen, fortfarande schemalagd.
    if (artikel is None or artikel["status"] != "schemalagd"
            or db.innehall_hash(artikel["innehall"]) != slot["godkand_hash"]
            or artikel["godkand_hash"] != slot["godkand_hash"]):
        _avsluta(slot, "avbokad", "granskas", "publicering_stoppad",
                 "Artikeln ändrades efter godkännandet. Publiceringen stoppades och artikeln måste granskas igen.",
                 godkand_at=None, godkand_hash=None)
        return

    lage = config.settings.publicering
    if lage == "simulering":
        _avsluta(slot, "simulerad", "godkand", "publicering_simulerad",
                 "Simulering: allt kontrollerades men inget skickades till WordPress.")
        return

    wp_status = "publish" if lage == "skarp" else "draft"
    try:
        async with WordPress(transport=transport) as wp:
            post = await wp.spara_inlagg(artikel, wp_status)
    except WordPressFel as e:
        forsok = slot["forsok"] + 1
        if forsok >= MAX_FORSOK:
            _avsluta(slot, "fel", "godkand", "publicering_misslyckad", f"{e} Gav upp efter {forsok} försök.")
        else:
            with db._conn() as c:
                _satt_slot(c, slot["id"], status="planerad", forsok=forsok, fel=str(e),
                           nasta_forsok=till_utc_text(nu + timedelta(minutes=5 * forsok)))
            db.logga(slot["artikel_id"], "publicering_forsok", f"Försök {forsok} misslyckades: {e}")
            log.warning("Publicering av artikel %s misslyckades (försök %s): %s", slot["artikel_id"], forsok, e)
        return

    if wp_status == "publish":
        _avsluta(slot, "publicerad", "publicerad", "publicerad", f"Publicerad: {post.get('link', '')}",
                 wp_post_id=post["id"], wp_lank=post.get("link"))
    else:
        _avsluta(slot, "utkast", "godkand", "wp_utkast_skapat",
                 f"Utkastläge: utkast skapat i WordPress (inlägg {post['id']}), inget publicerades.",
                 wp_post_id=post["id"])


async def kor_en_gang(nu: datetime | None = None, transport=None) -> None:
    """Går igenom kön en gång. Anropas av schemaläggaren och av testerna."""
    nu = nu or nu_utc()
    with db._conn() as c:
        rader = [dict(r) for r in c.execute(
            "SELECT * FROM publiceringar WHERE status = 'planerad' AND tid_utc <= ? "
            "AND (nasta_forsok IS NULL OR nasta_forsok <= ?) ORDER BY tid_utc",
            (till_utc_text(nu), till_utc_text(nu)),
        )]
    paus = pausad()
    for slot in rader:
        if date.fromisoformat(slot["datum"]) < idag_lokalt(nu):
            # Dagen har passerat (t.ex. paus eller driftstopp): publicera inte i efterhand.
            _avsluta(slot, "missad", "godkand", "publicering_missad",
                     "Publiceringsdagen passerade utan publicering. Schemalägg artikeln på nytt.")
        elif not paus:
            await _publicera(slot, nu, transport=transport)


# ---------- Säkerhetskopior ----------

def sakerhetskopiera(mal) -> None:
    """Konsistent kopia av databasen, även medan den används."""
    with db._conn() as c:
        dest = sqlite3.connect(mal)
        try:
            c.backup(dest)
        finally:
            dest.close()


def daglig_sakerhetskopia(nu: datetime | None = None, behall: int = 14) -> None:
    mapp = config.settings.data_dir / "backups"
    mapp.mkdir(parents=True, exist_ok=True)
    fil = mapp / f"studio-{idag_lokalt(nu).isoformat()}.db"
    if fil.exists():
        return
    sakerhetskopiera(fil)
    for gammal in sorted(mapp.glob("studio-*.db"))[:-behall]:
        gammal.unlink()
    log.info("Säkerhetskopia sparad: %s", fil.name)


async def schemalaggare() -> None:
    log.info("Schemaläggaren startad (läge: %s)", config.settings.publicering)
    while True:
        try:
            await kor_en_gang()
            await asyncio.to_thread(daglig_sakerhetskopia)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Fel i schemaläggaren")
        await asyncio.sleep(INTERVALL_SEKUNDER)
