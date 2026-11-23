"""
Invio ordini a Capital.com (API REST): demo con MODALITA_DEMO=1, reale solo con
MODALITA_REALE=1 e CONFERMA_REALE=SI_SOLDI_VERI.

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


CONFERMA_REALE = "SI_SOLDI_VERI"


def conto_reale() -> bool:
    """True solo con MODALITA_REALE=1 e la frase di conferma esatta; mai insieme al demo."""
    return os.getenv("MODALITA_REALE") == "1" and os.getenv("CONFERMA_REALE") == CONFERMA_REALE


def _base() -> str:
    demo = os.getenv("MODALITA_DEMO") == "1"
    reale = conto_reale()
    if demo and reale:
        raise RuntimeError("Configurazione ambigua: MODALITA_DEMO e MODALITA_REALE attive insieme.")
    if reale:
        return "https://api-capital.backend-capital.com/api/v1"
    if demo:
        return "https://demo-api-capital.backend-capital.com/api/v1"
    raise RuntimeError("Ordini bloccati: imposta MODALITA_DEMO=1, oppure MODALITA_REALE=1 con CONFERMA_REALE.")


def nome_conto() -> str:
    tipo = "REALE" if conto_reale() else "DEMO"
    if os.getenv("CAPITAL_SOTTOCONTO", "").strip():
        _sessione()
        return f"{tipo} – sottoconto {_SESSIONE_CACHE.get('account_name')}"
    return tipo


def _norm(testo: str) -> str:
    return "".join(c for c in (testo or "").lower() if c.isalnum())


def elenco_conti() -> list:
    """Sottoconti disponibili: nome, id, saldo (nessuna chiave)."""
    h = _sessione()
    r = requests.get(f"{_base()}/accounts", headers=h, timeout=20)
    r.raise_for_status()
    return r.json().get("accounts", [])


def equity_eur() -> float:
    """Saldo + profitto/perdita aperto del sottoconto su cui opera il bot."""
    h = _sessione()
    r = requests.get(f"{_base()}/accounts", headers=h, timeout=20)
    r.raise_for_status()
    conti = r.json().get("accounts", [])
    scelto = _SESSIONE_CACHE.get("account_id")
    conto = next((a for a in conti if a.get("accountId") == scelto), None)
    if conto is None:
        if os.getenv("CAPITAL_SOTTOCONTO", "").strip():
            raise RuntimeError("Sottoconto non trovato tra i conti: blocco per sicurezza.")
        conto = next((a for a in conti if a.get("preferred")), conti[0])
    b = conto["balance"]
    return float(b["balance"]) + float(b.get("profitLoss", 0))


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
    header = {
        "X-CAP-API-KEY": os.environ["CAPITAL_API_KEY"].strip(),
        "CST": r.headers["CST"],
        "X-SECURITY-TOKEN": r.headers["X-SECURITY-TOKEN"],
        "Content-Type": "application/json",
    }
    attuale = r.json().get("currentAccountId")
    _SESSIONE_CACHE["account_id"] = attuale
    nome = os.getenv("CAPITAL_SOTTOCONTO", "").strip()
    if nome:  # opera SOLO sul sottoconto indicato (per nome o per ID)
        a = requests.get(f"{_base()}/accounts", headers=header, timeout=20)
        a.raise_for_status()
        conti = a.json().get("accounts", [])
        conto = next((c for c in conti if c.get("accountId") == nome or _norm(c.get("accountName")) == _norm(nome)), None)
        if conto is None:
            nomi = ", ".join(c.get("accountName", "?") for c in conti)
            raise RuntimeError(f"Sottoconto '{nome}' non trovato. Conti disponibili: {nomi}. Nessun ordine inviato.")
        if conto["accountId"] != attuale:
            s = requests.put(f"{_base()}/session", headers=header, json={"accountId": conto["accountId"]}, timeout=20)
            if not s.ok and "error.not-different.accountId" not in s.text:
                raise RuntimeError(f"Cambio sottoconto fallito ({s.status_code}): {s.text[:200]}. Nessun ordine inviato.")
        _SESSIONE_CACHE["account_id"] = conto["accountId"]
        _SESSIONE_CACHE["account_name"] = conto.get("accountName")
    return header


def candele(risoluzione: str = "HOUR_4", quante: int = 300) -> list:
    """Ultime candele (prezzo medio bid/ask). Ogni elemento: (inizio UTC, open, high, low, close)."""
    h = _sessione()
    r = requests.get(f"{_base()}/prices/{EPIC}", headers=h, params={"resolution": risoluzione, "max": quante}, timeout=30)
    r.raise_for_status()
    m = lambda p: (float(p["bid"]) + float(p["ask"])) / 2
    return [(x["snapshotTimeUTC"], m(x["openPrice"]), m(x["highPrice"]), m(x["lowPrice"]), m(x["closePrice"]))
            for x in r.json().get("prices", [])]


def eurusd() -> float:
    h = _sessione()
    r = requests.get(f"{_base()}/markets/EURUSD", headers=h, timeout=20)
    r.raise_for_status()
    snap = r.json()["snapshot"]
    return (float(snap["bid"]) + float(snap["offer"])) / 2


def posizioni_gold() -> list:
    """Posizioni aperte sull'oro: dict con dealId, lato (1/-1), size, level, stopLevel, upl."""
    h = _sessione()
    r = requests.get(f"{_base()}/positions", headers=h, timeout=20)
    r.raise_for_status()
    out = []
    for p in r.json().get("positions", []):
        if p["market"]["epic"] != EPIC:
            continue
        pos = p["position"]
        out.append({
            "dealId": pos["dealId"], "lato": 1 if pos["direction"] == "BUY" else -1,
            "size": float(pos["size"]), "level": float(pos["level"]),
            "stopLevel": pos.get("stopLevel"), "upl": pos.get("upl"),
        })
    return out


def sposta_stop(deal_id: str, livello: float) -> None:
    h = _sessione()
    r = requests.put(f"{_base()}/positions/{deal_id}", headers=h,
                     json={"stopLevel": round(livello, DECIMALI_PREZZO), "guaranteedStop": False}, timeout=20)
    r.raise_for_status()


def chiudi_deal(deal_id: str) -> None:
    h = _sessione()
    r = requests.delete(f"{_base()}/positions/{deal_id}", headers=h, timeout=20)
    r.raise_for_status()


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
