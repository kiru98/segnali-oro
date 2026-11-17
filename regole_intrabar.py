"""
Regole a livello di ingresso (versione intrabar) sul prezzo del broker.

Filtri calcolati sulle candele da 15 minuti già chiuse; il segnale scatta quando
il prezzo bid/ask tocca il massimo (long) o minimo (short) della candela precedente.
Stessi parametri di segnali_oro.py: LOOKBACK 10, ATR 14 x 1.5, target 2R.
"""

import numpy as np
import pandas as pd

LOOKBACK = 10
ATR_MULT = 1.5
RR_TARGET = 2.0


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def rsi(s: pd.Series, n: int = 14) -> pd.Series:
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn)


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    pc = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def barre_da(cond: pd.Series) -> pd.Series:
    out = np.full(len(cond), 10**9)
    last = None
    for i, v in enumerate(cond.to_numpy()):
        if v:
            last = i
        if last is not None:
            out[i] = i - last
    return pd.Series(out, index=cond.index)


def prepara(m15: pd.DataFrame) -> pd.DataFrame:
    """m15: candele 15m chiuse, indice = inizio candela (UTC), colonne open/high/low/close."""
    h1 = m15.resample("1h", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()
    h1["fine"] = h1.index + pd.Timedelta("1h")
    h1["ema50"] = ema(h1["close"], 50)
    h1["ema200"] = ema(h1["close"], 200)
    h1 = h1.set_index("fine")[["close", "ema50", "ema200"]]

    c = m15.copy()
    c["fine"] = c.index + pd.Timedelta("15min")
    c["ema50"] = ema(c["close"], 50)
    c["rsi"] = rsi(c["close"])
    c["atr"] = atr(c)
    c["min_swing"] = c["low"].rolling(LOOKBACK).min()
    c["max_swing"] = c["high"].rolling(LOOKBACK).max()

    pos = h1.index.searchsorted(c["fine"].to_numpy(), side="right") - 1
    ok = pos >= 0
    for col in ("close", "ema50", "ema200"):
        v = np.full(len(c), np.nan)
        v[ok] = h1[col].to_numpy()[pos[ok]]
        c["d_" + col] = v
    c["trend_long"] = (c["d_close"] > c["d_ema200"]) & (c["d_ema50"] > c["d_ema200"])
    c["trend_short"] = (c["d_close"] < c["d_ema200"]) & (c["d_ema50"] < c["d_ema200"])
    c["tocco_long"] = barre_da(c["low"] <= c["ema50"]) <= LOOKBACK
    c["tocco_short"] = barre_da(c["high"] >= c["ema50"]) <= LOOKBACK
    c["rsi_long"] = barre_da(c["rsi"] < 40) <= LOOKBACK
    c["rsi_short"] = barre_da(c["rsi"] > 60) <= LOOKBACK
    # filtri valutati alla chiusura della candela: validi per la candela successiva
    c["pronto_long"] = (c["trend_long"] & c["tocco_long"] & c["rsi_long"]).fillna(False)
    c["pronto_short"] = (c["trend_short"] & c["tocco_short"] & c["rsi_short"]).fillna(False)
    return c.dropna(subset=["ema50", "atr", "d_ema200", "min_swing"])


def livelli(ultima: pd.Series, entry: float, lato: int) -> tuple[float, float]:
    """Stop e target dalla candela chiusa precedente, come in segnali_oro.py."""
    if lato == 1:
        sl = min(ultima["min_swing"], entry - ATR_MULT * ultima["atr"])
        tp = entry + RR_TARGET * abs(entry - sl)
    else:
        sl = max(ultima["max_swing"], entry + ATR_MULT * ultima["atr"])
        tp = entry - RR_TARGET * abs(entry - sl)
    return sl, tp


def scatta(ultima: pd.Series, bid: float, ask: float):
    """Ritorna (lato, prezzo_di_ingresso) se il prezzo tocca il livello, altrimenti None.
    Long: entra al prezzo di lettera (ask) quando sale oltre il massimo precedente.
    Short: entra al prezzo di denaro (bid) quando scende sotto il minimo precedente."""
    if ultima["pronto_long"] and ask >= ultima["high"]:
        return 1, ask
    if ultima["pronto_short"] and bid <= ultima["low"]:
        return -1, bid
    return None
