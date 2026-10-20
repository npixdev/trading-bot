"""Boucle 24/7 : appelle trader.step() en encaissant les erreurs réseau."""

from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable

import ccxt
import pandas as pd

from bot.realtime.store import Store

log = logging.getLogger(__name__)

HEARTBEAT_KEY = "heartbeat"  # lu par bot.monitoring.health (HEALTHCHECK Docker)

RECONNECT_AFTER = 3  # échecs consécutifs avant de recréer la connexion
MAX_BACKOFF_SECONDS = 300


def run_loop(
    trader,  # Trader (non typé pour éviter un import circulaire)
    feed,
    store: Store,
    poll_seconds: float,
    duration: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_step: Callable[[pd.Timestamp], None] | None = None,
) -> None:
    """Tourne jusqu'à `duration` secondes (None = indéfiniment) ou Ctrl+C.

    - Erreur réseau / exchange indisponible : journalisée, attente croissante, reconnexion après 3 échecs.
    - Toute autre exception (bug) : journalisée puis **relancée** → le bot s'arrête.
      Un bot qui continue après un bug inconnu est plus dangereux qu'un bot arrêté.
    """
    start = clock()
    failures = 0
    store.log_event(pd.Timestamp.now(tz="UTC"), "run_start", "boucle démarrée", commit=True)
    try:
        while duration is None or clock() - start < duration:
            try:
                trader.step()
                failures = 0
                now = pd.Timestamp.now(tz="UTC")
                store.set_meta(HEARTBEAT_KEY, now.isoformat())
                store.commit()
                if on_step is not None:
                    try:
                        on_step(now)
                    except Exception:  # noqa: BLE001 — un résumé raté ne doit pas arrêter le trading
                        log.exception("Erreur pendant le résumé quotidien (ignorée)")
                sleep(poll_seconds)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
                failures += 1
                delay = min(poll_seconds * 2**failures, MAX_BACKOFF_SECONDS)
                msg = f"{type(exc).__name__}: {exc} (échec n°{failures}, nouvel essai dans {delay:.0f}s)"
                log.warning(msg)
                store.log_event(pd.Timestamp.now(tz="UTC"), "network_error", msg[:500], commit=True)
                if failures >= RECONNECT_AFTER:
                    feed.reconnect()
                sleep(delay)
    except KeyboardInterrupt:
        log.info("Arrêt manuel (Ctrl+C) : l'état est déjà sauvegardé.")
        store.log_event(pd.Timestamp.now(tz="UTC"), "run_end", "arrêt manuel (Ctrl+C)", commit=True)
    except Exception as exc:
        store.log_event(
            pd.Timestamp.now(tz="UTC"), "crash", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}",
            commit=True,
        )
        log.error("Erreur inattendue, arrêt du bot : %s", exc)
        raise
    else:
        store.log_event(pd.Timestamp.now(tz="UTC"), "run_end", "durée écoulée", commit=True)
