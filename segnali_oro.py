"""
Segnali oro – Pullback nel trend (piano "Trading in the Zone").

Ogni esecuzione:
  1. scarica i prezzi dell'oro (futures GC=F da Yahoo Finance, gratis);
  2. costruisce le candele a 4 ore e calcola EMA, RSI e ATR;
  3. simula le regole del piano su tutto lo storico disponibile;
  4. manda su Telegram solo gli eventi NUOVI (setup, +1R, stop, target, stop temporale).

Variabili d'ambiente richieste:
  TELEGRAM_TOKEN    token del bot (da @BotFather) – va nei Secrets di GitHub
  TELEGRAM_CHAT_ID  il tuo chat_id
Opzionali:
  CAPITALE_EUR (default 300), RISCHIO_PCT (default 0.5), TEST_MESSAGE=1 per un messaggio di prova.

Il filtro notizie (CPI, NFP, FOMC) NON è automatico: va controllato a mano.
"""

import json
import math
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

# ───────── Parametri del piano ─────────
SIMBOLO = "GC=F"          # futures oro COMEX (prezzi molto vicini a XAU/USD, non identici)
LOOKBACK = 10             # barre massime per il pullback
ATR_MULT = 1.5            # distanza minima dello stop in ATR
RR_TARGET = 2.0           # target in R
MAX_BARRE = 60            # stop temporale: 60 barre da 4 ore = 10 giorni
OZ_PER_LOTTO = 100        # CFD XAU/USD: 1 lotto = 100 once
LOTTO_MINIMO = 0.01
ROMA = ZoneInfo("Europe/Rome")
TF = "4H"                 # etichetta del timeframe nei messaggi
PROMEMORIA = "Prima di entrare: notizie CPI/NFP/FOMC nelle prossime 2 ore? Checklist completa?"
SETUP = []  # setup generati da simula(), usati per inviare gli ordini al conto demo

CAPITALE_EUR = float(os.getenv("CAPITALE_EUR", "300"))
RISCHIO_PCT = float(os.getenv("RISCHIO_PCT", "0.5"))
STATO = Path(os.getenv("STATO_FILE", "stato.json"))


# ───────── Indicatori ─────────
def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rma(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    delta = close.diff()
    up = rma(delta.clip(lower=0), n)
    down = rma(-delta.clip(upper=0), n)
    rs = up / down.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    prev = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev).abs(), (df["low"] - prev).abs()], axis=1
    ).max(axis=1)
    return rma(tr, n)


def barre_da(cond: pd.Series) -> pd.Series:
    """Equivalente di ta.barssince: barre trascorse dall'ultima volta che cond era vera."""
    out, ultimo = [], None
    for i, v in enumerate(cond.to_numpy()):
        if v:
            ultimo = i
        out.append(np.nan if ultimo is None else i - ultimo)
    return pd.Series(out, index=cond.index)


# ───────── Dati ─────────
def scarica_dati():
    import yfinance as yf

    h = yf.download(SIMBOLO, period="60d", interval="1h", auto_adjust=False, progress=False)
    d = yf.download(SIMBOLO, period="2y", interval="1d", auto_adjust=False, progress=False)
    fx = yf.download("EURUSD=X", period="5d", interval="1d", auto_adjust=False, progress=False)
    for x in (h, d, fx):
        if isinstance(x.columns, pd.MultiIndex):
            x.columns = x.columns.get_level_values(0)
        x.columns = [c.lower() for c in x.columns]
    eurusd = float(fx["close"].dropna().iloc[-1]) if len(fx) else 1.10
    return h, d, eurusd


def candele_4h(h: pd.DataFrame, adesso: datetime) -> pd.DataFrame:
    h = h.copy()
    h.index = pd.to_datetime(h.index, utc=True)
    c = h.resample("4h", origin="epoch", label="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    c["fine"] = c.index + pd.Timedelta(hours=4)
    return c[c["fine"] <= adesso]          # solo candele chiuse


def prepara(c4: pd.DataFrame, d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    d["ema50"] = ema(d["close"], 50)
    d["ema200"] = ema(d["close"], 200)

    c = c4.copy()
    c["ema50"] = ema(c["close"], 50)
    c["rsi"] = rsi(c["close"])
    c["atr"] = atr(c)
    c["min_swing"] = c["low"].rolling(LOOKBACK).min()
    c["max_swing"] = c["high"].rolling(LOOKBACK).max()

    # Trend giornaliero: valori dell'ultimo giorno CHIUSO prima della candela
    giorno = c.index.tz_convert(None).normalize()
    prec = d[["close", "ema50", "ema200"]].shift(0)
    idx = prec.index.searchsorted(giorno, side="left") - 1
    valido = idx >= 0
    for col in ("close", "ema50", "ema200"):
        vals = np.full(len(c), np.nan)
        vals[valido] = prec[col].to_numpy()[idx[valido]]
        c["d_" + col] = vals
    c["trend_long"] = (c["d_close"] > c["d_ema200"]) & (c["d_ema50"] > c["d_ema200"])
    c["trend_short"] = (c["d_close"] < c["d_ema200"]) & (c["d_ema50"] < c["d_ema200"])

    c["tocco_long"] = barre_da(c["low"] <= c["ema50"]) <= LOOKBACK
    c["tocco_short"] = barre_da(c["high"] >= c["ema50"]) <= LOOKBACK
    c["rsi_long"] = barre_da(c["rsi"] < 40) <= LOOKBACK
    c["rsi_short"] = barre_da(c["rsi"] > 60) <= LOOKBACK
    c["trig_long"] = (c["close"] > c["high"].shift(1)) & (c["rsi"] > 40)
    c["trig_short"] = (c["close"] < c["low"].shift(1)) & (c["rsi"] < 60)

    fine_roma = c["fine"].dt.tz_convert(ROMA)
    c["fine_settimana"] = (fine_roma.dt.dayofweek == 4) & (fine_roma.dt.hour >= 18)
    return c.dropna(subset=["ema50", "atr", "d_ema200", "min_swing"])


# ───────── Simulazione delle regole ─────────
def px(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def dimensione(r_usd_per_oncia: float, eurusd: float):
    rischio_eur = CAPITALE_EUR * RISCHIO_PCT / 100
    rischio_usd = rischio_eur * eurusd
    lotti = math.floor(rischio_usd / (r_usd_per_oncia * OZ_PER_LOTTO) * 100) / 100
    rischio_min_eur = r_usd_per_oncia * OZ_PER_LOTTO * LOTTO_MINIMO / eurusd
    return rischio_eur, lotti, rischio_min_eur


def simula(c: pd.DataFrame, eurusd: float):
    eventi = []
    pos, entry, sl, tp, r, bar_entry, pareggio = 0, None, None, None, None, None, False
    righe = c.reset_index(names="inizio")
    for i, b in righe.iterrows():
        quando = b["fine"]
        if pos != 0:
            lato = "LONG" if pos == 1 else "SHORT"
            stop_hit = b["low"] <= sl if pos == 1 else b["high"] >= sl
            target_hit = b["high"] >= tp if pos == 1 else b["low"] <= tp
            if stop_hit:
                esito = "pareggio" if pareggio else "-1R"
                eventi.append((quando, f"ORO {lato} – STOP colpito a {px(sl)} ({esito})\nRegistra il trade nel diario."))
                pos = 0
            elif target_hit:
                eventi.append((quando, f"ORO {lato} – TARGET raggiunto a {px(tp)} (+{RR_TARGET:g}R)\nRegistra il trade nel diario."))
                pos = 0
            elif i - bar_entry >= MAX_BARRE or b.get("chiudi_sessione", False):
                eventi.append((quando, f"ORO {lato} – STOP TEMPORALE: chiudi a mercato (~{px(b['close'])})"))
                pos = 0
            elif not pareggio and (b["high"] >= entry + r if pos == 1 else b["low"] <= entry - r):
                sl, pareggio = entry, True
                eventi.append((quando, f"ORO {lato} – +1R raggiunto: sposta lo stop a pareggio {px(entry)}"))

        if pos != 0 or b["fine_settimana"] or b.get("blocco_news", False) or not b.get("sessione", True):
            continue
        long_ = b["trend_long"] and b["tocco_long"] and b["rsi_long"] and b["trig_long"]
        short_ = b["trend_short"] and b["tocco_short"] and b["rsi_short"] and b["trig_short"]
        if not (long_ or short_):
            continue
        pos = 1 if long_ else -1
        entry = b["close"]
        if long_:
            sl = min(b["min_swing"], entry - ATR_MULT * b["atr"])
        else:
            sl = max(b["max_swing"], entry + ATR_MULT * b["atr"])
        r = abs(entry - sl)
        tp = entry + RR_TARGET * r if long_ else entry - RR_TARGET * r
        bar_entry, pareggio = i, False
        rischio_eur, lotti, rischio_min_eur = dimensione(r, eurusd)
        lato = "LONG" if long_ else "SHORT"
        testo = (
            f"SETUP {lato} ORO ({TF})\n"
            f"Entrata ~ {px(entry)} (apertura prossima candela)\n"
            f"Stop: {px(sl)}  (distanza {px(r)} $)\n"
            f"Target {RR_TARGET:g}R: {px(tp)}\n"
        )
        if lotti >= LOTTO_MINIMO:
            testo += f"Rischio {RISCHIO_PCT:g}% = {px(rischio_eur)} € -> lotti: {lotti:.2f}\n"
        else:
            perc = rischio_min_eur / CAPITALE_EUR * 100
            testo += (
                f"ATTENZIONE: il rischio previsto è {px(rischio_eur)} €, ma anche il lotto minimo "
                f"(0,01) rischia ~{px(rischio_min_eur)} € = {perc:.0f}% del conto.\n"
                f"Secondo il piano questo trade NON va fatto con soldi veri: usalo in demo.\n"
            )
        testo += PROMEMORIA
        eventi.append((quando, testo))
        SETUP.append({"quando": quando, "lato": lato, "entry": entry, "sl": sl, "tp": tp, "lotti": lotti})
    return eventi


# ───────── Telegram e stato ─────────
def invia(testo: str):
    nome = os.getenv("NOME_STRATEGIA", "").strip()
    if nome:  # etichetta per capire quale strategia ha mandato il messaggio
        testo = f"[{nome}]\n{testo}"
    token = os.environ["TELEGRAM_TOKEN"]
    chat = os.environ["TELEGRAM_CHAT_ID"]
    resp = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat, "text": testo},
        timeout=20,
    )
    if not resp.ok:
        print("Errore Telegram:", resp.status_code, resp.text[:200])
        resp.raise_for_status()


def leggi_stato():
    if STATO.exists():
        return json.loads(STATO.read_text())
    return {}


def main():
    if os.getenv("TEST_MESSAGE") == "1":
        invia("Test riuscito: il bot dei segnali oro è collegato.")
        print("Messaggio di prova inviato.")
        return

    adesso = datetime.now(timezone.utc)
    h, d, eurusd = scarica_dati()
    c = prepara(candele_4h(h, adesso), d)
    if c.empty:
        print("Dati insufficienti.")
        return
    eventi = simula(c, eurusd)
    notifica(eventi, c, adesso)


def notifica(eventi, c, adesso):
    stato = leggi_stato()
    ultimo = stato.get("ultimo_evento")
    ultimo = pd.Timestamp(ultimo) if ultimo else None

    if ultimo is None:
        # Prima esecuzione: non manda lo storico, registra solo il punto di partenza.
        stato["ultimo_evento"] = c["fine"].iloc[-1].isoformat()
        print(f"Prima esecuzione: {len(eventi)} eventi storici ignorati.")
        invia(f"Bot segnali oro {TF} attivo. Ti scriverò solo quando le regole del piano si verificano.")
    else:
        nuovi = [(t, m) for t, m in eventi if t > ultimo]
        for t, m in nuovi:
            invia(m)
            print("Inviato:", t, m.splitlines()[0])
        stato["ultimo_evento"] = max([ultimo, c["fine"].iloc[-1]]).isoformat()
        print(f"Eventi nuovi: {len(nuovi)}")

    STATO.write_text(json.dumps(stato, indent=2))


if __name__ == "__main__":
    sys.exit(main())
