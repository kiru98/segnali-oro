"""
Segnali oro – versione intraday a 15 minuti.

Stesse regole del piano, scalate:
  - trend: grafico a 1 ora (EMA 50 e EMA 200 sull'ultima candela oraria CHIUSA);
  - pullback, RSI e trigger: grafico a 15 minuti;
  - attivo 24 ore su 24 (quando il mercato dell'oro è aperto);
  - niente entrate da 30 min prima a 30 min dopo i dati USD ad alto impatto
    (calendario economico scaricato in automatico, nessun messaggio di notizie).
"""

import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import notizie
import segnali_oro as base

base.TF = "15m"
base.MAX_BARRE = 32                     # 8 ore al massimo
base.RISCHIO_PCT = float(os.getenv("RISCHIO_PCT_15M", "3"))
base.STATO = Path("stato_15m.json")
base.PROMEMORIA = "Checklist completa? Controlla il prezzo sul tuo broker prima di entrare."
MARGINE_NEWS = timedelta(minutes=30)


def scarica_dati():
    import yfinance as yf

    m = yf.download(base.SIMBOLO, period="30d", interval="15m", auto_adjust=False, progress=False)
    fx = yf.download("EURUSD=X", period="5d", interval="1d", auto_adjust=False, progress=False)
    for x in (m, fx):
        if isinstance(x.columns, pd.MultiIndex):
            x.columns = x.columns.get_level_values(0)
        x.columns = [c.lower() for c in x.columns]
    eurusd = float(fx["close"].dropna().iloc[-1]) if len(fx) else 1.10
    return m, eurusd


def candele(m: pd.DataFrame, regola: str, adesso: datetime) -> pd.DataFrame:
    m = m.copy()
    m.index = pd.to_datetime(m.index, utc=True)
    c = m.resample(regola, origin="epoch", label="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    ).dropna()
    c["fine"] = c.index + pd.Timedelta(regola)
    return c[c["fine"] <= adesso]


def prepara(c15: pd.DataFrame, c1h: pd.DataFrame, eventi_news) -> pd.DataFrame:
    h = c1h.copy()
    h["ema50"] = base.ema(h["close"], 50)
    h["ema200"] = base.ema(h["close"], 200)
    h = h.set_index("fine")[["close", "ema50", "ema200"]]

    c = c15.copy()
    c["ema50"] = base.ema(c["close"], 50)
    c["rsi"] = base.rsi(c["close"])
    c["atr"] = base.atr(c)
    c["min_swing"] = c["low"].rolling(base.LOOKBACK).min()
    c["max_swing"] = c["high"].rolling(base.LOOKBACK).max()

    # trend dall'ultima candela oraria chiusa entro la fine della candela da 15 min
    pos = h.index.searchsorted(c["fine"].to_numpy(), side="right") - 1
    ok = pos >= 0
    for col in ("close", "ema50", "ema200"):
        v = np.full(len(c), np.nan)
        v[ok] = h[col].to_numpy()[pos[ok]]
        c["d_" + col] = v
    c["trend_long"] = (c["d_close"] > c["d_ema200"]) & (c["d_ema50"] > c["d_ema200"])
    c["trend_short"] = (c["d_close"] < c["d_ema200"]) & (c["d_ema50"] < c["d_ema200"])

    L = base.LOOKBACK
    c["tocco_long"] = base.barre_da(c["low"] <= c["ema50"]) <= L
    c["tocco_short"] = base.barre_da(c["high"] >= c["ema50"]) <= L
    c["rsi_long"] = base.barre_da(c["rsi"] < 40) <= L
    c["rsi_short"] = base.barre_da(c["rsi"] > 60) <= L
    c["trig_long"] = (c["close"] > c["high"].shift(1)) & (c["rsi"] > 40)
    c["trig_short"] = (c["close"] < c["low"].shift(1)) & (c["rsi"] < 60)

    c["sessione"] = True                 # nessun limite di orario
    c["chiudi_sessione"] = False
    c["fine_settimana"] = False

    blocco = np.zeros(len(c), dtype=bool)
    fine = c["fine"].to_numpy()
    for e in eventi_news:
        t = np.datetime64(pd.Timestamp(e["utc"]).tz_convert("UTC").tz_localize(None))
        fine_naive = pd.to_datetime(fine).tz_convert("UTC").tz_localize(None).to_numpy()
        blocco |= (fine_naive >= t - MARGINE_NEWS) & (fine_naive <= t + MARGINE_NEWS)
    c["blocco_news"] = blocco
    return c.dropna(subset=["ema50", "atr", "d_ema200", "min_swing"])


def main():
    if os.getenv("TEST_MESSAGE") == "1":
        base.invia("Test riuscito: versione 15 minuti collegata.")
        return
    if os.getenv("TEST_ORDINE") == "1":
        import ordini
        ordini.prova()
        return
    adesso = datetime.now(timezone.utc)
    m, eurusd = scarica_dati()
    eventi_news = notizie.calendario(aggiorna=True)
    c = prepara(candele(m, "15min", adesso), candele(m, "1h", adesso), eventi_news)
    if c.empty:
        print("Dati insufficienti.")
        return
    eventi = base.simula(c, eurusd)
    ultimo = base.leggi_stato().get("ultimo_evento")
    base.notifica(eventi, c, adesso)
    if ultimo:  # alla prima esecuzione non si inviano ordini per lo storico
        import ordini
        ordini.esegui([s for s in base.SETUP if s["quando"] > pd.Timestamp(ultimo)])


if __name__ == "__main__":
    sys.exit(main())
