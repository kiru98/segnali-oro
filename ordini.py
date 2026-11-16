"""
Collega i setup dei segnali agli ordini sul conto demo.

ORDINI_ATTIVI=1 invia davvero gli ordini; altrimenti manda solo un messaggio
"ORDINE SIMULATO", così si può verificare il flusso prima di attivarlo.
"""

import os

import broker_oanda as broker
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
            base.invia(f"Ordine inviato al conto DEMO: {descrizione}")
        except Exception as e:  # errore del broker: lo segnalo, non fermo il bot
            base.invia(f"ERRORE ordine demo: {e} | {descrizione}")
