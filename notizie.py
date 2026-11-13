"""
Notizie di mercato per l'oro.

- Calendario economico della settimana (fonte gratuita ForexFactory/FairEconomy):
  tiene gli eventi USD ad alto impatto e li salva in calendario.json, che la
  versione a 15 minuti usa per bloccare le entrate vicino alle notizie.
- Titoli recenti sull'oro da feed RSS pubblici, con un "tono" indicativo
  calcolato contando parole rialziste/ribassiste. Non è un'analisi fondamentale:
  serve solo come promemoria del contesto della giornata.

Uso: python notizie.py  -> aggiorna calendario.json e manda il riepilogo su Telegram.
"""

import json
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

ROMA = ZoneInfo("Europe/Rome")
CALENDARIO = Path("calendario.json")
URL_CAL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
FEED = [
    "https://news.google.com/rss/search?q=gold+price+OR+XAUUSD+when:1d&hl=en-US&gl=US&ceid=US:en",
    "https://www.fxstreet.com/rss/news",
]
UA = {"User-Agent": "Mozilla/5.0 (segnali-oro)"}

# radici: contano una volta sola anche al plurale o con -s (rise/rises, gain/gains...)
RIALZO = ["rise", "rall", "gain", "surge", "jump", "record high", "climb", "higher",
          "safe-haven demand", "rate cut"]
RIBASSO = ["fall", "drop", "slide", "slump", "decline", "lower", "plunge", "rate hike",
           "hawkish", "profit-taking", "retreat"]


# ───────── Calendario ─────────
def scarica_calendario():
    r = requests.get(URL_CAL, headers=UA, timeout=20)
    r.raise_for_status()
    eventi = []
    for e in r.json():
        if e.get("country") != "USD" or e.get("impact") != "High":
            continue
        try:
            quando = datetime.fromisoformat(e["date"]).astimezone(timezone.utc)
        except (KeyError, ValueError):
            continue
        eventi.append({"titolo": e.get("title", ""), "utc": quando.isoformat(),
                       "previsione": e.get("forecast", ""), "precedente": e.get("previous", "")})
    return eventi


def calendario(aggiorna=True):
    """Restituisce gli eventi USD ad alto impatto; li riscarica al massimo una volta al giorno."""
    oggi = datetime.now(ROMA).date().isoformat()
    if CALENDARIO.exists():
        dati = json.loads(CALENDARIO.read_text())
        if dati.get("giorno") == oggi or not aggiorna:
            return dati.get("eventi", [])
    if not aggiorna:
        return []
    try:
        eventi = scarica_calendario()
    except Exception as exc:                      # la fonte può essere irraggiungibile
        print("Calendario non disponibile:", exc)
        return json.loads(CALENDARIO.read_text()).get("eventi", []) if CALENDARIO.exists() else []
    CALENDARIO.write_text(json.dumps({"giorno": oggi, "eventi": eventi}, indent=2, ensure_ascii=False))
    return eventi


# ───────── Titoli ─────────
def pulisci(t):
    return unescape(re.sub(r"<[^>]+>", "", t or "")).strip()


def scarica_titoli(max_titoli=8):
    titoli, visti = [], set()
    for url in FEED:
        try:
            r = requests.get(url, headers=UA, timeout=20)
            r.raise_for_status()
            radice = ET.fromstring(r.content)
        except Exception as exc:
            print("Feed non disponibile:", url, exc)
            continue
        for item in radice.iter("item"):
            t = pulisci(item.findtext("title"))
            if not t:
                continue
            if "fxstreet" in url and not re.search(r"\b(gold|xau)\b", t, re.I):
                continue
            chiave = t.lower()[:60]
            if chiave in visti:
                continue
            visti.add(chiave)
            titoli.append({"titolo": t, "link": (item.findtext("link") or "").strip()})
    return titoli[:max_titoli]


def tono(titoli):
    su = giu = 0
    for t in titoli:
        testo = " " + t["titolo"].lower() + " "
        # il dollaro si muove al contrario dell'oro: conta prima queste frasi e toglile
        for frase, verso in (("weaker dollar", 1), ("dollar falls", 1), ("dollar slides", 1),
                             ("stronger dollar", -1), ("dollar rises", -1), ("dollar gains", -1)):
            n = testo.count(frase)
            su, giu = su + n * (verso > 0), giu + n * (verso < 0)
            testo = testo.replace(frase, " ")
        su += sum(testo.count(" " + p) for p in RIALZO)
        giu += sum(testo.count(" " + p) for p in RIBASSO)
    if su == giu:
        return "neutro / misto"
    return "prevalentemente rialzista" if su > giu else "prevalentemente ribassista"


# ───────── Messaggio ─────────
def riepilogo():
    eventi = calendario(aggiorna=True)
    oggi = datetime.now(ROMA).date()
    di_oggi = [e for e in eventi if datetime.fromisoformat(e["utc"]).astimezone(ROMA).date() == oggi]
    titoli = scarica_titoli()

    righe = [f"NOTIZIE ORO – {oggi.strftime('%d/%m/%Y')}", ""]
    if di_oggi:
        righe.append("Dati USD ad alto impatto oggi (ora italiana):")
        for e in di_oggi:
            ora = datetime.fromisoformat(e["utc"]).astimezone(ROMA).strftime("%H:%M")
            extra = f" (prev. {e['previsione']}, prec. {e['precedente']})" if e["previsione"] else ""
            righe.append(f"• {ora} {e['titolo']}{extra}")
        righe.append("La versione 15 min non apre trade da 30 min prima a 30 min dopo questi orari.")
    else:
        righe.append("Nessun dato USD ad alto impatto oggi.")
    righe.append("")
    if titoli:
        righe.append(f"Titoli recenti sull'oro – tono {tono(titoli)} (indicativo):")
        for t in titoli:
            righe.append(f"• {t['titolo']}")
    else:
        righe.append("Titoli non disponibili oggi.")
    righe += ["", "Ricorda: le notizie danno il contesto, il segnale lo danno solo le regole del piano."]
    return "\n".join(righe)


def main():
    from segnali_oro import invia
    testo = riepilogo()
    print(testo)
    invia(testo[:4000])


if __name__ == "__main__":
    sys.exit(main())
