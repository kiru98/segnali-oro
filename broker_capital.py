"""
Invio ordini al conto DEMO di Capital.com (API REST).

Sicurezza:
  - funziona solo con MODALITA_DEMO=1 e usa sempre il server demo;
  - un ordine alla volta: se c'è già una posizione sull'oro, non ne apre un'altra;
  - stop loss e take profit vengono impostati sull'ordine, quindi il broker
    chiude la posizione da solo anche se il bot non gira.

Non gestisce: spostamento a pareggio a +1R e stop temporale (restano sul messaggio Telegram).
"""

import os

import requests

EPIC = "GOLD"
DECIMALI_PREZZO = 2  # da verificare sul conto demo


def _base() -> str:
    if os.getenv("MODALITA_DEMO") != "1":
        raise RuntimeError("Ordini bloccati: MODALITA_DEMO deve essere 1 (solo conto demo).")
    return "https://demo-api-capital.backend-capital.com/api/v1"


_SESSIONE_CACHE = {"header": None, "quando": 0.0}
DURATA_SESSIONE_SECONDI = 300  # riusa il login per 5 minuti: evita il limite di richieste di Capital.com


def _sessione() -> dict:
    """Riusa la sessione per qualche minuto: un login per ogni controllo fa scattare il limite (429)."""
    import time

    if _SESSIONE_CACHE["header"] and time.time() - _SESSIONE_CACHE["quando"] < DURATA_SESSIONE_SECONDI:
        return dict(_SESSIONE_CACHE["header"])
    header = _login()
    _SESSIONE_CACHE.update({"header": header, "quando": time.time()})
    return dict(header)


def _login() -> dict:
    r = requests.post(
        f"{_base()}/session",
        headers={"X-CAP-API-KEY": os.environ["CAPITAL_API_KEY"].strip(), "Content-Type": "application/json"},
        json={
            "identifier": os.environ["CAPITAL_IDENTIFIER"].strip(),
            "password": os.environ["CAPITAL_API_PASSWORD"].strip(),
            "encryptedPassword": False,
        },
        timeout=20,
    )
    if not r.ok:
        raise RuntimeError(f"login Capital.com fallito ({r.status_code}): {r.text[:300]}")
    return {
        "X-CAP-API-KEY": os.environ["CAPITAL_API_KEY"].strip(),
        "CST": r.headers["CST"],
        "X-SECURITY-TOKEN": r.headers["X-SECURITY-TOKEN"],
        "Content-Type": "application/json",
    }


def conta_posizioni_gold() -> int:
    h = _sessione()
    r = requests.get(f"{_base()}/positions", headers=h, timeout=20)
    r.raise_for_status()
    return sum(1 for p in r.json().get("positions", []) if p["market"]["epic"] == EPIC)


def posizione_aperta() -> bool:
    h = _sessione()
    r = requests.get(f"{_base()}/positions", headers=h, timeout=20)
    r.raise_for_status()
    for p in r.json().get("positions", []):
        if p["market"]["epic"] == EPIC:
            return True
    return False


def prezzo_bid_ask() -> tuple[float, float]:
    """Denaro e lettera attuali dal broker."""
    h = _sessione()
    r = requests.get(f"{_base()}/markets/{EPIC}", headers=h, timeout=20)
    r.raise_for_status()
    snap = r.json()["snapshot"]
    return float(snap["bid"]), float(snap["offer"])


def apri(lato: str, dimensione: int, distanza_sl: float, distanza_tp: float) -> dict:
    """Ordine a mercato. Stop e target sono distanze dal prezzo di riempimento reale,
    così il rischio in dollari resta quello del segnale."""
    h = _sessione()
    body = {
        "epic": EPIC,
        "direction": "BUY" if lato == "LONG" else "SELL",
        "size": dimensione,
        "stopDistance": round(distanza_sl, DECIMALI_PREZZO),
        "profitDistance": round(distanza_tp, DECIMALI_PREZZO),
        "guaranteedStop": False,
    }
    r = requests.post(f"{_base()}/positions", headers=h, json=body, timeout=20)
    r.raise_for_status()
    ref = r.json()["dealReference"]
    # conferma che l'ordine sia stato accettato, non solo ricevuto
    c = requests.get(f"{_base()}/confirms/{ref}", headers=h, timeout=20)
    c.raise_for_status()
    esito = c.json()
    if esito.get("dealStatus") != "ACCEPTED":
        raise RuntimeError(f"ordine non accettato: {esito.get('reason', esito)}")
    return esito


def chiudi() -> int:
    h = _sessione()
    r = requests.get(f"{_base()}/positions", headers=h, timeout=20)
    r.raise_for_status()
    chiuse = 0
    for p in r.json().get("positions", []):
        if p["market"]["epic"] == EPIC:
            d = requests.delete(f"{_base()}/positions/{p['position']['dealId']}", headers=h, timeout=20)
            d.raise_for_status()
            chiuse += 1
    return chiuse
