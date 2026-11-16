"""
Collega i setup dei segnali agli ordini sul conto demo.

ORDINI_ATTIVI=1 invia davvero gli ordini; altrimenti manda solo un messaggio
"ORDINE SIMULATO", così si può verificare il flusso prima di attivarlo.
"""

import os

import broker_capital as broker
import segnali_oro as base


def esegui(setup_nuovi: list) -> None:
    for s in setup_nuovi:
        unita = int(round(s["lotti"] * base.OZ_PER_LOTTO))
        descrizione = (
            f"{s['lato']} ORO {unita} oz | entrata ~{base.px(s['entry'])} | "
            f"stop {base.px(s['sl'])} | target {base.px(s['tp'])}"
        )
        if unita < 1:
            base.invia(f"Ordine NON inviato (dimensione sotto il minimo): {descrizione}")
            continue
        if os.getenv("ORDINI_ATTIVI") != "1":
            base.invia(f"ORDINE SIMULATO (ordini disattivati): {descrizione}")
            continue
        try:
            if broker.posizione_aperta():
                base.invia(f"Ordine NON inviato: c'è già una posizione aperta. {descrizione}")
                continue
            broker.apri(s["lato"], unita, s["sl"], s["tp"])
            base.invia(f"Ordine inviato al conto DEMO Capital.com: {descrizione}")
        except Exception as e:  # errore del broker: lo segnalo, non fermo il bot
            base.invia(f"ERRORE ordine demo: {e} | {descrizione}")


def prova() -> None:
    """Test manuale dal workflow: verifica la connessione al demo e, con ORDINI_ATTIVI=1,
    apre e chiude subito un ordine minimo su GOLD."""
    try:
        aperta = broker.posizione_aperta()
        base.invia(f"Test Capital.com demo: connessione OK. Posizione GOLD aperta: {'sì' if aperta else 'no'}.")
        if os.getenv("ORDINI_ATTIVI") != "1":
            base.invia("Test ordine NON eseguito: ORDINI_ATTIVI non è 1. Verificata solo la connessione.")
            return
        if aperta:
            base.invia("Test ordine NON eseguito: c'è già una posizione GOLD aperta.")
            return
        h, _, _ = base.scarica_dati()
        prezzo = float(h["close"].iloc[-1])
        dim = int(os.getenv("TEST_DIMENSIONE", "1"))
        broker.apri("LONG", dim, round(prezzo * 0.99, 2), round(prezzo * 1.01, 2))
        base.invia(f"Test: ordine di prova aperto ({dim} unità a ~{base.px(prezzo)}).")
        chiuse = broker.chiudi()
        base.invia(f"Test: ordine di prova chiuso ({chiuse} posizioni).")
    except Exception as e:
        base.invia(f"ERRORE test Capital.com: {e}")
