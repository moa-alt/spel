"""Bakgrundsjobb för artikelgenerering.

Genereringen körs på servern och fortsätter även om webbläsaren stängs.
Förloppet hålls i minnet; resultatet sparas i databasen.
"""

import asyncio
import html
import logging

from . import db, lankforslag, seo_ai
from .ai_writer import GenereringsFel, generera_artikel
from . import config
from .demo import build_demo_article

log = logging.getLogger("studio.jobb")

MAX_SAMTIDIGA = 2
_semafor = asyncio.Semaphore(MAX_SAMTIDIGA)
_uppgifter: dict[int, asyncio.Task] = {}
# artikel-id -> {"steg": int, "tecken": int}
forlopp: dict[int, dict] = {}


def pagar(artikel_id: int) -> bool:
    task = _uppgifter.get(artikel_id)
    return bool(task and not task.done())


def starta(artikel_id: int) -> bool:
    """Köar generering. Returnerar False om artikeln inte kan genereras just nu."""
    if pagar(artikel_id):
        return False
    if not db.byt_status(artikel_id, ("utkast", "granskas", "fel"), "genereras", fel=None):
        return False
    forlopp[artikel_id] = {"steg": -1, "tecken": 0}
    _uppgifter[artikel_id] = asyncio.create_task(_kor(artikel_id))
    return True


async def _kor(artikel_id: int) -> None:
    try:
        async with _semafor:
            artikel = db.hamta(artikel_id)
            if artikel is None:
                return
            huvud, sek = artikel["huvudsokord"], artikel["sekundara_sokord"]
            # Begärda ändringar skickas med så att AI:n reviderar den tidigare versionen.
            andringar = artikel["andringar"]
            tidigare = artikel["innehall"] if andringar else None
            forlopp[artikel_id] = {"steg": 0, "tecken": 0}
            # Internlänkar: bara sidor som finns i den senaste kartläggningen av sajten.
            lankmal = seo_ai.lankmal_for(artikel)
            if not config.settings.anthropic_api_key:
                resultat = build_demo_article(huvud, sek)
                if lankmal:
                    lista = "".join(f'<li><a href="{html.escape(m["url"])}">{html.escape(m["titel"])}</a></li>' for m in lankmal[:3])
                    resultat["html"] += f"\n<h2 id=\"las-mer\">Läs mer</h2>\n<ul>{lista}</ul>"
            else:
                resultat = None
                async for h in generera_artikel(huvud, sek, andringar=andringar, tidigare=tidigare, lankmal=lankmal):
                    if h["typ"] == "steg":
                        forlopp[artikel_id]["steg"] = h["steg"]
                    elif h["typ"] == "framsteg":
                        forlopp[artikel_id]["tecken"] = h["tecken"]
                    elif h["typ"] == "klar":
                        resultat = h["artikel"]
                if resultat is None:
                    raise GenereringsFel("Genereringen avslutades utan resultat. Försök igen.")
            resultat["html"], resultat["internlankar"] = seo_ai.kontrollera_lankar(
                resultat["html"], {m["url"] for m in lankmal})
            db.byt_status(artikel_id, ("genereras",), "granskas", innehall=resultat, fel=None, andringar=None)
            # Befintliga artiklar som kan länka till den nya; blir godkännbara först när den är publicerad.
            lankforslag.for_ny_artikel(db.hamta(artikel_id))
            db.logga(artikel_id, "genererad", "Med begärda ändringar" if andringar else None, db.innehall_hash(resultat))
    except GenereringsFel as e:
        db.byt_status(artikel_id, ("genereras",), "fel", fel=str(e))
    except asyncio.CancelledError:
        db.byt_status(artikel_id, ("genereras",), "fel", fel="Genereringen avbröts.")
        raise
    except Exception:
        # Loggar bara undantagstypen och spårningen; inga nycklar finns i artikeldata.
        log.exception("Oväntat fel vid generering av artikel %s", artikel_id)
        db.byt_status(artikel_id, ("genereras",), "fel", fel="Ett oväntat fel inträffade. Försök igen.")
    finally:
        forlopp.pop(artikel_id, None)
        _uppgifter.pop(artikel_id, None)
