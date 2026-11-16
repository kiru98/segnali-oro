"""
Invio ordini al conto DEMO di OANDA (API REST v20).

Sicurezza:
  - funziona solo con MODALITA_DEMO=1 e usa sempre il server di prova (practice);
  - un ordine alla volta: se c'è già una posizione aperta, non ne apre un'altra;
  - stop loss e take profit vengono impostati sull'ordine, così il broker chiude
    la posizione da solo anche se il bot non gira.

Non gestisce: spostamento a pareggio a +1R e stop temporale. Quegli eventi restano
solo sul messaggio Telegram e vanno gestiti a mano sul conto demo.
"""

import os

import requests

STRUMENTO = "XAU_USD"
DECIMALI_PREZZO = 2  # da verificare con GET /instruments sul tuo conto demo


def _base() -> str:
    if os.getenv("MODALITA_DEMO") != "1":
        raise RuntimeError("Ordini bloccati: MODALITA_DEMO deve essere 1 (solo conto demo).")
    return "https://api-fxpractice.oanda.com/v3"


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {os.environ['OANDA_API_KEY']}",
        "Content-Type": "application/json",
    }


def _account() -> str:
    return os.environ["OANDA_ACCOUNT_ID"]


def posizione_aperta() -> bool:
    r = requests.get(f"{_base()}/accounts/{_account()}/openPositions", headers=_headers(), timeout=20)
    r.raise_for_status()
    for p in r.json().get("positions", []):
        if p["instrument"] == STRUMENTO:
            if float(p["long"]["units"]) != 0 or float(p["short"]["units"]) != 0:
                return True
    return False


def apri(lato: str, unita: int, sl: float, tp: float) -> dict:
    segno = 1 if lato == "LONG" else -1
    body = {
        "order": {
            "type": "MARKET",
            "instrument": STRUMENTO,
            "units": str(segno * unita),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {"price": f"{sl:.{DECIMALI_PREZZO}f}"},
            "takeProfitOnFill": {"price": f"{tp:.{DECIMALI_PREZZO}f}"},
        }
    }
    r = requests.post(f"{_base()}/accounts/{_account()}/orders", headers=_headers(), json=body, timeout=20)
    r.raise_for_status()
    return r.json()
