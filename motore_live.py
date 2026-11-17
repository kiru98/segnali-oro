"""
Motore in tempo reale: legge il prezzo del broker in continuo e apre l'ordine
appena il prezzo tocca il livello della candela precedente.

Va eseguito su un server sempre acceso (non su GitHub Actions).

  python motore_live.py            -> live, legge Capital.com ogni pochi secondi
  python motore_live.py --replay F -> prova su un CSV di candele (nessun ordine)

ORDINI_ATTIVI=1 invia davvero gli ordini al conto demo; altrimenti solo messaggi.
"""

import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

import regole_intrabar as regole

INTERVALLO_SECONDI = float(os.getenv("INTERVALLO_SECONDI", "2"))
CAPITALE_EUR = float(os.getenv("CAPITALE_EUR", "300"))
RISCHIO_PCT = float(os.getenv("RISCHIO_PCT_15M", "3"))
OZ_PER_LOTTO = 100
SCOSTAMENTO_MAX = 5.0  # dollari tra prezzo del tick e prezzo di riempimento


MAX_BARRE_POSIZIONE = 32  # come nella strategia: dopo 32 candele da 15 minuti la posizione scade


class Stato:
    def __init__(self, storico: pd.DataFrame):
        self.chiuse = storico.sort_index()  # candele 15m chiuse
        self.corrente = None  # dict: inizio, open, high, low, close
        self.in_posizione = False
        self.scadenza = None  # istante oltre il quale la posizione non è più considerata aperta
        self._prep = None  # cache dei filtri, ricalcolata solo a candela chiusa

    def aggiungi_tick(self, t: datetime, prezzo: float):
        """Costruisce le candele 15m dai tick. Ritorna la candela appena chiusa, se c'è."""
        inizio = pd.Timestamp(t).floor("15min")
        chiusa = None
        if self.corrente is None or inizio != self.corrente["inizio"]:
            if self.corrente is not None:
                chiusa = self._chiudi()
            self.corrente = {"inizio": inizio, "open": prezzo, "high": prezzo, "low": prezzo, "close": prezzo}
        else:
            c = self.corrente
            c["high"] = max(c["high"], prezzo)
            c["low"] = min(c["low"], prezzo)
            c["close"] = prezzo
        return chiusa

    def _chiudi(self):
        c = self.corrente
        riga = pd.DataFrame(
            [{"open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"]}],
            index=[c["inizio"]],
        )
        self.chiuse = pd.concat([self.chiuse, riga])
        self._prep = None
        return riga

    def ultima_chiusa(self) -> pd.Series:
        """Ultima candela chiusa con i filtri già calcolati (cache fino alla prossima chiusura)."""
        if self._prep is None:
            self._prep = regole.prepara(self.chiuse)
        return self._prep.iloc[-1]


def dimensione(distanza_sl: float, eurusd: float = 1.10) -> int:
    rischio_usd = CAPITALE_EUR * RISCHIO_PCT / 100 * eurusd
    lotti = int(rischio_usd / (distanza_sl * OZ_PER_LOTTO) * 100) / 100
    return int(round(lotti * OZ_PER_LOTTO))


def gestisci_tick(stato: Stato, t: datetime, bid: float, ask: float, invia_ordine):
    """Applica la regola a un tick. invia_ordine(lato, prezzo, sl, tp, unita) apre l'ordine."""
    stato.aggiungi_tick(t, (bid + ask) / 2)
    if stato.in_posizione and stato.scadenza is not None and pd.Timestamp(t) >= stato.scadenza:
        stato.in_posizione = False
    if stato.in_posizione or len(stato.chiuse) < 300:
        return None
    ultima = stato.ultima_chiusa()
    esito = regole.scatta(ultima, bid, ask)
    if esito is None:
        return None
    lato, entrata = esito
    sl, tp = regole.livelli(ultima, entrata, lato)
    unita = dimensione(abs(entrata - sl))
    stato.in_posizione = True
    stato.scadenza = pd.Timestamp(t).floor("15min") + pd.Timedelta(minutes=15 * MAX_BARRE_POSIZIONE)
    invia_ordine(lato, entrata, sl, tp, unita)
    return esito


def replay(path: str):
    """Riproduce le candele del CSV come tick (apertura, massimo, minimo, chiusura)."""
    df = pd.read_csv(path, parse_dates=["time"]).set_index("time").sort_index()
    storico = df.iloc[:300]
    stato = Stato(storico)
    eventi = []

    def finto_ordine(lato, prezzo, sl, tp, unita):
        eventi.append((lato, prezzo, sl, tp, unita))

    for t, r in df.iloc[300:].iterrows():
        for p in (r["open"], r["high"], r["low"], r["close"]):
            gestisci_tick(stato, t.to_pydatetime(), p, p, finto_ordine)
    print(f"Ingressi simulati: {len(eventi)}")
    for e in eventi[:5]:
        print("  lato", e[0], "entrata", round(e[1], 2), "stop", round(e[2], 2), "target", round(e[3], 2), "oz", e[4])


def live():
    import broker_capital as broker
    import ordini  # noqa: F401  (messaggi Telegram)
    import scarica_storico

    storico = scarica_storico.scarica()
    storico["time"] = pd.to_datetime(storico["time"])
    storico = storico.set_index("time")[["open", "high", "low", "close"]]
    stato = Stato(storico)
    attivi = os.getenv("ORDINI_ATTIVI") == "1"

    def invia(lato, prezzo, sl, tp, unita):
        nome = "LONG" if lato == 1 else "SHORT"
        if not attivi or unita < 1:
            print("SIMULATO", nome, prezzo, sl, tp, unita)
            return
        esito = broker.apri(nome, unita, abs(prezzo - sl), abs(tp - prezzo))
        print("INVIATO", nome, esito.get("level"), esito.get("stopLevel"), esito.get("profitLevel"))

    while True:
        try:
            bid, ask = broker.prezzo_bid_ask()
            gestisci_tick(stato, datetime.now(timezone.utc), bid, ask, invia)
        except Exception as e:
            print("Errore:", e)
        time.sleep(INTERVALLO_SECONDI)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--replay":
        replay(sys.argv[2])
    else:
        live()
