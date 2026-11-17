"""
Collega i setup dei segnali agli ordini sul conto demo.

ORDINI_ATTIVI=1 invia davvero gli ordini; altrimenti manda solo un messaggio
"ORDINE SIMULATO", così si può verificare il flusso prima di attivarlo.
"""

import os

import broker_capital as broker
import segnali_oro as base


def _avvisa(testo: str) -> None:
    """Invia su Telegram senza far fallire il programma se la connessione cade."""
    try:
        base.invia(testo)
    except Exception as e:
        print("Telegram non raggiungibile:", e)


def esegui(setup_nuovi: list) -> None:
    for s in setup_nuovi:
        unita = int(round(base.LOTTO_MINIMO * base.OZ_PER_LOTTO))  # sempre 0.01 lotti = 1 oz
        descrizione = (
            f"{s['lato']} ORO {unita} oz | entrata ~{base.px(s['entry'])} | "
            f"stop {base.px(s['sl'])} | target {base.px(s['tp'])}"
        )
        if unita < 1:
            _avvisa(f"Ordine NON inviato (dimensione sotto il minimo): {descrizione}")
            continue
        if os.getenv("ORDINI_ATTIVI") != "1":
            _avvisa(f"ORDINE SIMULATO (ordini disattivati): {descrizione}")
            continue
        try:
            if broker.posizione_aperta():
                _avvisa(f"Ordine NON inviato: c'è già una posizione aperta. {descrizione}")
                continue
            broker.apri(s["lato"], unita, abs(s["entry"] - s["sl"]), abs(s["tp"] - s["entry"]))
            _avvisa(f"Ordine inviato al conto DEMO Capital.com: {descrizione}")
        except Exception as e:  # errore del broker: lo segnalo, non fermo il bot
            _avvisa(f"ERRORE ordine demo: {e} | {descrizione}")


def prova() -> None:
    """Test manuale dal workflow: verifica la connessione al demo e, con ORDINI_ATTIVI=1,
    apre e chiude subito un ordine minimo su GOLD."""
    try:
        aperta = broker.posizione_aperta()
        _avvisa(f"Test Capital.com demo: connessione OK. Posizione GOLD aperta: {'sì' if aperta else 'no'}.")
        if os.getenv("ORDINI_ATTIVI") != "1":
            _avvisa("Test ordine NON eseguito: ORDINI_ATTIVI non è 1. Verificata solo la connessione.")
            return
        if aperta:
            _avvisa("Test ordine NON eseguito: c'è già una posizione GOLD aperta.")
            return
        h, _, _ = base.scarica_dati()
        prezzo = float(h["close"].iloc[-1])
        dim = int(os.getenv("TEST_DIMENSIONE", "1"))
        broker.apri("LONG", dim, round(prezzo * 0.01, 2), round(prezzo * 0.01, 2))
        _avvisa(f"Test: ordine di prova aperto ({dim} unità a ~{base.px(prezzo)}).")
        chiuse = broker.chiudi()
        _avvisa(f"Test: ordine di prova chiuso ({chiuse} posizioni).")
    except Exception as e:
        _avvisa(f"ERRORE test Capital.com: {e}")
