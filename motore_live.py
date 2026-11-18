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
MAX_POSIZIONI = int(os.getenv("MAX_POSIZIONI", "1"))  # quante operazioni aperte in contemporanea


class Stato:
    def __init__(self, storico: pd.DataFrame):
        self.chiuse = storico.sort_index()  # candele 15m chiuse
        self.corrente = None  # dict: inizio, open, high, low, close
        self.scadenze = []  # lista di (istante di apertura, istante di scadenza) delle posizioni locali
        self.bar_entrata = None  # candela in cui è già stato aperto un ordine (un ingresso per candela)
        self.armato = {1: True, -1: True}  # False dopo un ingresso: si riarma quando il segnale si esaurisce
        self.broker_aperte = None  # posizioni GOLD aperte sul broker, aggiornate ogni 30 secondi nel live
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
    """Sempre 0.01 lotti = 1 oz per operazione."""
    return 1


def posizioni_occupate(stato: Stato, ora: pd.Timestamp) -> int:
    """Quante operazioni risultano aperte adesso.
    Nel live usa il conteggio del broker più le aperture degli ultimi 2 minuti (il broker può essere in ritardo)."""
    if stato.broker_aperte is None:
        return len(stato.scadenze)
    recenti = sum(1 for apertura, _ in stato.scadenze if ora - apertura < pd.Timedelta(minutes=2))
    return stato.broker_aperte + recenti


def gestisci_tick(stato: Stato, t: datetime, bid: float, ask: float, invia_ordine):
    """Applica la regola a un tick. invia_ordine(lato, prezzo, sl, tp, unita) apre l'ordine."""
    stato.aggiungi_tick(t, (bid + ask) / 2)
    ora = pd.Timestamp(t)
    stato.scadenze = [(a, sc) for a, sc in stato.scadenze if sc > ora]
    if len(stato.chiuse) < 300:
        return None
    if posizioni_occupate(stato, ora) >= MAX_POSIZIONI:
        return None
    barra = stato.corrente["inizio"]
    if stato.bar_entrata == barra:  # al massimo un ingresso per candela
        return None
    ultima = stato.ultima_chiusa()
    if not ultima["pronto_long"]:
        stato.armato[1] = True
    if not ultima["pronto_short"]:
        stato.armato[-1] = True
    esito = regole.scatta(ultima, bid, ask)
    if esito is None:
        return None
    lato, entrata = esito
    if not stato.armato[lato]:  # stesso segnale già usato: non apro di nuovo
        return None
    stato.armato[lato] = False
    sl, tp = regole.livelli(ultima, entrata, lato)
    unita = dimensione(abs(entrata - sl))
    stato.bar_entrata = barra
    stato.scadenze.append((ora, ora.floor("15min") + pd.Timedelta(minutes=15 * MAX_BARRE_POSIZIONE)))
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
    import ordini
    import scarica_storico
    import segnali_oro as base

    storico = scarica_storico.scarica()
    storico["time"] = pd.to_datetime(storico["time"])
    storico = storico.set_index("time")[["open", "high", "low", "close"]]
    stato = Stato(storico)
    attivi = os.getenv("ORDINI_ATTIVI") == "1"

    def invia(lato, prezzo, sl, tp, unita):
        nome = "LONG" if lato == 1 else "SHORT"
        descrizione = (
            f"{nome} ORO {unita} oz | entrata ~{base.px(prezzo)} | "
            f"stop {base.px(sl)} | target {base.px(tp)}"
        )
        if not attivi or unita < 1:
            print("SIMULATO", descrizione, flush=True)
            ordini._avvisa(f"ORDINE SIMULATO (ordini disattivati): {descrizione}")
            return
        try:
            esito = broker.apri(nome, unita, abs(prezzo - sl), abs(tp - prezzo))
            print("INVIATO", descrizione, flush=True)
            ordini._avvisa(f"Ordine inviato al conto DEMO Capital.com: {descrizione} | riempito a {esito.get('level')}")
        except Exception as e:
            print("ERRORE", e, flush=True)
            ordini._avvisa(f"ERRORE ordine demo: {e} | {descrizione}")

    ultimo_conteggio = 0.0
    while True:
        try:
            if time.time() - ultimo_conteggio > 30:
                stato.broker_aperte = broker.conta_posizioni_gold()
                ultimo_conteggio = time.time()
            bid, ask = broker.prezzo_bid_ask()
            gestisci_tick(stato, datetime.now(timezone.utc).replace(tzinfo=None), bid, ask, invia)
        except Exception as e:
            print("Errore:", e)
        time.sleep(INTERVALLO_SECONDI)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--replay":
        replay(sys.argv[2])
    else:
        live()
