"""
Scarica lo storico a 15 minuti dell'oro dal conto DEMO Capital.com e lo salva in CSV.

Non apre né chiude ordini: serve solo per il backtest.
Prezzo = punto medio tra denaro (bid) e lettera (ask).
"""

import os
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import broker_capital as broker

RISOLUZIONE = "MINUTE_15"
GIORNI = int(os.getenv("GIORNI_STORICO", "60"))
MAX_PER_CHIAMATA = 1000
FILE_OUT = "storico_gold_15m.csv"


def _data(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def _chiedi(h: dict, base: str, params: dict) -> list:
    r = requests.get(f"{base}/prices/{broker.EPIC}", headers=h, params=params, timeout=30)
    if not r.ok:
        raise RuntimeError(f"prezzi storici {r.status_code}: {r.text[:300]}")
    return r.json().get("prices", [])


def scarica() -> pd.DataFrame:
    """Scarica a ritroso: la prima chiamata prende le ultime candele, poi si usa 'to'
    con la candela più vecchia per andare più indietro, fino a coprire GIORNI."""
    h = broker._sessione()
    base = broker._base()
    inizio = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=GIORNI)
    righe = []
    params = {"resolution": RISOLUZIONE, "max": MAX_PER_CHIAMATA}
    for _ in range(100):  # tetto di sicurezza
        prezzi = _chiedi(h, base, params)
        if not prezzi:
            break
        for p in prezzi:
            righe.append({
                "time": p["snapshotTimeUTC"],
                "open": (p["openPrice"]["bid"] + p["openPrice"]["ask"]) / 2,
                "high": (p["highPrice"]["bid"] + p["highPrice"]["ask"]) / 2,
                "low": (p["lowPrice"]["bid"] + p["lowPrice"]["ask"]) / 2,
                "close": (p["closePrice"]["bid"] + p["closePrice"]["ask"]) / 2,
            })
        piu_vecchia = min(datetime.fromisoformat(p["snapshotTimeUTC"].replace("Z", "")) for p in prezzi)
        if piu_vecchia <= inizio:
            break
        params = {**params, "to": _data(piu_vecchia)}
    df = pd.DataFrame(righe).drop_duplicates("time").sort_values("time")
    return df


if __name__ == "__main__":
    df = scarica()
    df.to_csv(FILE_OUT, index=False)
    print(f"Salvate {len(df)} candele in {FILE_OUT}")
    if len(df):
        print("Dal", df["time"].iloc[0], "al", df["time"].iloc[-1])
