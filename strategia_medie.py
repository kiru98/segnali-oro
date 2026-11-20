"""
STRATEGIA MEDIE 10/30 – oro su candele da 4 ore (Capital.com).

Regole (le stesse del backtest su dati Capital.com, giugno 2021 – novembre 2026):
  - entra in acquisto quando la media esponenziale a 10 incrocia sopra quella a 30,
    in vendita quando incrocia sotto (alla chiusura della candela da 4 ore);
  - stop iniziale a 4 ATR(14) dal prezzo di entrata, poi segue il prezzo a ogni
    chiusura (non torna mai indietro);
  - esce all'incrocio contrario e, nello stesso momento, entra nella direzione opposta;
  - dimensione = RISCHIO_MEDIE % del capitale perso se tocca lo stop, arrotondata
    per difetto a 0,01 oz (minimo 0,01 oz).

Impostazioni (.env):
  ORDINI_MEDIE=1        invia ordini veri (altrimenti solo messaggi "SIMULATO")
  RISCHIO_MEDIE=4       % del capitale rischiata per operazione
  CALO_MAX_MEDIE=35     % di calo dal capitale iniziale oltre cui smette di aprire
Usa il conto (demo o reale) scelto da MODALITA_DEMO / MODALITA_REALE.
"""

import json
import math
import os
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

import broker_capital as broker
import segnali_oro as base

os.environ.setdefault("NOME_STRATEGIA", "STRATEGIA MEDIE 10/30")
K_ATR = 4.0
RISCHIO = float(os.getenv("RISCHIO_MEDIE", "4")) / 100
CALO_MAX = float(os.getenv("CALO_MAX_MEDIE", "35")) / 100
ATTIVI = os.getenv("ORDINI_MEDIE") == "1"
FILE_STATO = os.getenv("FILE_STATO_MEDIE", "stato_medie.json")
PAUSA = 60  # secondi tra un controllo e l'altro


def avvisa(testo: str) -> None:
    print(testo, flush=True)
    try:
        base.invia(testo)
    except Exception as e:
        print("Telegram non raggiungibile:", e, flush=True)


def leggi_stato() -> dict:
    try:
        return json.load(open(FILE_STATO))
    except Exception:
        return {}


def salva_stato(s: dict) -> None:
    json.dump(s, open(FILE_STATO, "w"))


def indicatori(candele: list) -> pd.DataFrame:
    d = pd.DataFrame(candele, columns=["inizio", "open", "high", "low", "close"])
    d["inizio"] = pd.to_datetime(d["inizio"], utc=True)
    d = d.sort_values("inizio").drop_duplicates("inizio").reset_index(drop=True)
    d["ema10"] = d["close"].ewm(span=10, adjust=False).mean()
    d["ema30"] = d["close"].ewm(span=30, adjust=False).mean()
    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return d


def segnale(d: pd.DataFrame) -> int:
    """1 = incrocio rialzista sull'ultima candela chiusa, -1 = ribassista, 0 = niente."""
    a, b = d.iloc[-2], d.iloc[-1]
    if b["ema10"] > b["ema30"] and a["ema10"] <= a["ema30"]:
        return 1
    if b["ema10"] < b["ema30"] and a["ema10"] >= a["ema30"]:
        return -1
    return 0


def dimensione_oz(equity_eur: float, distanza_stop: float, cambio: float) -> float:
    return math.floor(equity_eur * RISCHIO * cambio / distanza_stop * 100) / 100


def passo(stato: dict) -> dict:
    adesso = datetime.now(timezone.utc)
    d = indicatori(broker.candele("HOUR_4", 300))
    d = d[d["inizio"] + timedelta(hours=4) <= adesso]  # solo candele chiuse
    if len(d) < 60:
        return stato
    ultima = d.iloc[-1]
    if stato.get("ultima_candela") == ultima["inizio"].isoformat():
        return stato  # già elaborata
    stato["ultima_candela"] = ultima["inizio"].isoformat()
    conto = broker.nome_conto()
    posizioni = broker.posizioni_gold()
    mia = next((p for p in posizioni if p["dealId"] == stato.get("deal")), None)

    # posizione chiusa dal broker (stop toccato)
    if stato.get("deal") and mia is None:
        avvisa(f"Posizione chiusa dal broker (stop) sul conto {conto}.")
        stato.update({"deal": None, "lato": 0, "sl": None})

    sig = segnale(d)
    lato = stato.get("lato", 0)

    # uscita all'incrocio contrario
    if mia and ((lato == 1 and ultima["ema10"] < ultima["ema30"]) or (lato == -1 and ultima["ema10"] > ultima["ema30"])):
        if ATTIVI:
            broker.chiudi_deal(mia["dealId"])
        avvisa(f"Chiusa {'LONG' if lato == 1 else 'SHORT'} ORO {mia['size']} oz per incrocio contrario "
               f"(risultato aperto prima della chiusura: {mia.get('upl')}) – conto {conto}")
        stato.update({"deal": None, "lato": 0, "sl": None})
        mia = None
        posizioni = broker.posizioni_gold()  # aggiorna: la posizione appena chiusa non deve bloccare l'entrata opposta

    # stop che segue il prezzo
    if mia:
        nuovo = ultima["close"] - K_ATR * ultima["atr"] if lato == 1 else ultima["close"] + K_ATR * ultima["atr"]
        vecchio = stato.get("sl")
        if vecchio is None or (lato == 1 and nuovo > vecchio + 0.01) or (lato == -1 and nuovo < vecchio - 0.01):
            if ATTIVI:
                broker.sposta_stop(mia["dealId"], nuovo)
            stato["sl"] = nuovo
            if ATTIVI:
                print(f"Stop spostato a {nuovo:.2f}", flush=True)

    # nuova entrata
    if sig and not mia:
        altre = [p for p in posizioni if p["dealId"] != stato.get("deal")]
        if altre:
            avvisa(f"Segnale {'LONG' if sig == 1 else 'SHORT'} NON eseguito: c'è già un'altra posizione sull'oro "
                   f"nel conto {conto} (aperta a mano o da un altro bot). Il conto non permette long e short insieme.")
            return stato
        equity = broker.equity_eur()
        stato.setdefault("capitale_iniziale", equity)
        if equity < stato["capitale_iniziale"] * (1 - CALO_MAX):
            avvisa(f"Segnale ignorato: capitale {equity:.2f} € sotto il limite di calo del {CALO_MAX*100:.0f}% "
                   f"(partenza {stato['capitale_iniziale']:.2f} €).")
            return stato
        dist = K_ATR * ultima["atr"]
        cambio = broker.eurusd()
        oz = dimensione_oz(equity, dist, cambio)
        nome = "LONG" if sig == 1 else "SHORT"
        rischio_eur = oz * dist / cambio
        testo = (f"{nome} ORO {oz:.2f} oz | prezzo ~{base.px(ultima['close'])} | stop a {dist:.2f} $ "
                 f"(~{rischio_eur:.2f} € = {RISCHIO*100:.0f}% di {equity:.2f} €) | uscita all'incrocio contrario")
        if oz < 0.01:
            avvisa(f"Segnale {nome} ignorato: dimensione sotto 0,01 oz. {testo}")
            return stato
        if not ATTIVI:
            avvisa(f"ORDINE SIMULATO (ORDINI_MEDIE non attivo): {testo}")
            return stato
        esito = broker.apri(nome, oz, dist, dist * 50)  # target molto lontano: si esce con incrocio o stop
        stato.update({"deal": esito.get("dealId") or esito.get("affectedDeals", [{}])[0].get("dealId"),
                      "lato": sig, "sl": esito.get("stopLevel")})
        avvisa(f"Ordine inviato al conto {conto}: {testo} | riempito a {esito.get('level')}")
    return stato


def main():
    stato = leggi_stato()
    avvisa(f"Avviata sul conto {broker.nome_conto()}. Ordini {'ATTIVI' if ATTIVI else 'SIMULATI'}, "
           f"rischio {RISCHIO*100:.0f}% per operazione, stop {K_ATR:.0f} ATR, candele 4 ore.")
    while True:
        try:
            stato = passo(stato)
            salva_stato(stato)
        except Exception as e:
            print("Errore:", e, flush=True)
        time.sleep(PAUSA)


if __name__ == "__main__":
    main()
