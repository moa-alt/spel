"""Sökordsplanering: CSV-import och kontroll av liknande artiklar."""

import csv
import io
import re

MAX_SEKUNDARA = 15
RUBRIKER = {"huvudsökord", "huvudsokord", "sökord", "sokord", "keyword", "main keyword", "primary keyword"}

# Vanliga ord som inte säger något om ämnet.
STOPPORD = {
    "i", "på", "pa", "för", "for", "och", "att", "en", "ett", "med", "av", "till", "om", "hur", "vad",
    "som", "det", "den", "de", "är", "ar", "vid", "från", "fran", "eller", "bästa", "basta", "bäst",
    "bast", "guide", "tips",
}
# Enkel svensk stamning: tar bort vanliga böjningsändelser så att "värmepumpar" ≈ "värmepump".
ANDELSER = ("arna", "erna", "orna", "ens", "ets", "ar", "er", "or", "en", "et", "na", "ns", "s", "n", "a", "e")


def normalisera_sokord(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _stam(ord_: str) -> str:
    for andelse in ANDELSER:
        if len(ord_) - len(andelse) >= 4 and ord_.endswith(andelse):
            return ord_[: -len(andelse)]
    return ord_


def _token(text: str) -> set[str]:
    ord_ = re.findall(r"[a-zåäöéü0-9]+", text.lower())
    return {_stam(o) for o in ord_ if o not in STOPPORD}


def likhet(a: str, b: str) -> float:
    """0–1. Jaccard-likhet mellan sökordens stammar; 1.0 om det ena sökordet ryms helt i det andra."""
    ta, tb = _token(a), _token(b)
    if not ta or not tb:
        return 0.0
    if ta <= tb or tb <= ta:
        return 1.0
    return len(ta & tb) / len(ta | tb)


GRANS_LIKNANDE = 0.5


def liknande(artiklar: list[dict]) -> dict[int, list[dict]]:
    """För varje artikel: andra artiklar i kön vars huvudsökord liknar dess eget."""
    resultat: dict[int, list[dict]] = {a["id"]: [] for a in artiklar}
    for i, a in enumerate(artiklar):
        for b in artiklar[i + 1:]:
            poang = likhet(a["huvudsokord"], b["huvudsokord"])
            if poang >= GRANS_LIKNANDE:
                resultat[a["id"]].append({"id": b["id"], "huvudsokord": b["huvudsokord"], "poang": round(poang, 2)})
                resultat[b["id"]].append({"id": a["id"], "huvudsokord": a["huvudsokord"], "poang": round(poang, 2)})
    return resultat


class CsvFel(ValueError):
    pass


def _avkoda(data: bytes) -> str:
    for kodning in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(kodning)
        except UnicodeDecodeError:
            continue
    raise CsvFel("Filen kunde inte läsas. Spara den som CSV (UTF-8).")


def tolka_csv(data: bytes) -> list[tuple[str, list[str]]]:
    """En artikel per rad: första kolumnen är huvudsökordet, resten sekundära sökord.

    Fungerar med semikolon (svensk Excel), komma och tabb som avgränsare. Sekundära sökord
    kan stå i egna kolumner eller i samma cell, åtskilda med komma eller |.
    """
    text = _avkoda(data)
    if not text.strip():
        raise CsvFel("Filen är tom.")
    try:
        dialekt = csv.Sniffer().sniff(text[:4096], delimiters=";,\t")
        avgransare = dialekt.delimiter
    except csv.Error:
        avgransare = ";" if ";" in text else ","

    rader = []
    for rad in csv.reader(io.StringIO(text), delimiter=avgransare):
        celler = [c.strip() for c in rad]
        if not celler or not celler[0]:
            continue
        if not rader and celler[0].lower() in RUBRIKER:
            continue
        huvud = normalisera_sokord(celler[0])
        sek: list[str] = []
        for cell in celler[1:]:
            for delord in re.split(r"[|,]" if avgransare != "," else r"\|", cell):
                delord = normalisera_sokord(delord)
                if delord and delord.lower() != huvud.lower() and delord.lower() not in {s.lower() for s in sek}:
                    sek.append(delord)
        if len(huvud) < 2 or len(huvud) > 120:
            continue
        rader.append((huvud, sek[:MAX_SEKUNDARA]))
    if not rader:
        raise CsvFel("Hittade inga sökord. Första kolumnen ska innehålla huvudsökordet.")
    return rader
