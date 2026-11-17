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


def scarica() -> pd.DataFrame:
    h = broker._sessione()
    base = broker._base()
    fine = datetime.now(timezone.utc).replace(tzinfo=None)
    inizio = fine - timedelta(days=GIORNI)
    righe = []
    cursore = inizio
    while cursore < fine:
        r = requests.get(
            f"{base}/prices/{broker.EPIC}",
            headers=h,
            params={"resolution": RISOLUZIONE, "max": MAX_PER_CHIAMATA, "from": _data(cursore), "to": _data(fine)},
            timeout=30,
        )
        r.raise_for_status()
        prezzi = r.json().get("prices", [])
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
        ultimo = datetime.fromisoformat(prezzi[-1]["snapshotTimeUTC"].replace("Z", ""))
        if ultimo <= cursore:
            break  # niente avanzamento: evito un ciclo infinito
        cursore = ultimo + timedelta(minutes=15)
    df = pd.DataFrame(righe).drop_duplicates("time").sort_values("time")
    return df


if __name__ == "__main__":
    df = scarica()
    df.to_csv(FILE_OUT, index=False)
    print(f"Salvate {len(df)} candele in {FILE_OUT}")
    if len(df):
        print("Dal", df["time"].iloc[0], "al", df["time"].iloc[-1])
