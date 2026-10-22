"""Boucle 24/7 : tour complet (trader.step) à intervalle régulier ET à chaque clôture de bougie,
stop-loss vérifié entre deux tours sur chaque prix poussé par le flux WebSocket.

Rythme :
- tour complet (signal, ordres, soldes, heartbeat) : toutes les `poll_seconds` (filet de sécurité)
  et `settle_seconds` après chaque clôture de bougie (décision prise quelques secondes après la clôture) ;
- entre deux tours, avec un flux : `trader.on_tick(prix)` à chaque nouveau prix (≈ 6/s sur OKX),
  sans aucune requête à l'exchange ; sans flux : simple attente.
"""

from __future__ import annotations

import logging
import math
import time
import traceback
from collections.abc import Callable

import ccxt
import pandas as pd

from bot.data.fetcher import timeframe_ms
from bot.realtime.store import Store

log = logging.getLogger(__name__)

HEARTBEAT_KEY = "heartbeat"  # lu par bot.monitoring.health (HEALTHCHECK Docker)

RECONNECT_AFTER = 3  # échecs consécutifs avant de recréer la connexion
MAX_BACKOFF_SECONDS = 300
LATE_CANDLE_RETRY_SECONDS = 2  # bougie pas encore publiée (OKX : ~10 s de retard mesuré) : on revient voir vite
LATE_CANDLE_MAX_RETRIES = 30  # … pendant une minute au plus, puis rythme normal
STREAM_DOWN_ALERT_SECONDS = 60


def seconds_until_next_close(timeframe: str, wall_now: float, settle_seconds: float) -> float:
    """Secondes avant `settle_seconds` après la prochaine clôture de bougie (bougies alignées sur l'epoch UTC)."""
    tf = timeframe_ms(timeframe) / 1000
    next_close = (math.floor(wall_now / tf) + 1) * tf
    target = next_close + settle_seconds
    if target - tf > wall_now:  # on est encore dans la fenêtre de « settle » de la clôture précédente
        target -= tf
    return max(0.0, target - wall_now)


def _awaited_bar_missing(trader, timeframe: str, wall_now: float, settle_seconds: float) -> bool:
    """True si la dernière bougie close (à l'horloge) n'a pas encore été traitée par le trader."""
    last_bar = getattr(trader, "last_bar", None)
    if last_bar is None:
        return False
    tf = timeframe_ms(timeframe) / 1000
    latest_close = math.floor((wall_now - settle_seconds) / tf) * tf
    return last_bar.timestamp() + tf < latest_close


def run_loop(
    trader,  # Trader (non typé pour éviter un import circulaire)
    feed,
    store: Store,
    poll_seconds: float,
    duration: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    on_step: Callable[[pd.Timestamp], None] | None = None,
    stream=None,  # PriceStream optionnel
    timeframe: str | None = None,  # active le réveil à la clôture des bougies
    settle_seconds: float = 3.0,
    wall_clock: Callable[[], float] = time.time,
) -> None:
    """Tourne jusqu'à `duration` secondes (None = indéfiniment) ou Ctrl+C.

    - Erreur réseau / exchange indisponible : journalisée, attente croissante, reconnexion après 3 échecs.
    - Toute autre exception (bug) : journalisée puis **relancée** → le bot s'arrête.
      Un bot qui continue après un bug inconnu est plus dangereux qu'un bot arrêté.
    """
    start = clock()
    failures = 0
    next_full = start
    late_retries = 0
    seen_version = 0
    store.log_event(pd.Timestamp.now(tz="UTC"), "run_start", "boucle démarrée", commit=True)
    try:
        while duration is None or clock() - start < duration:
            try:
                if clock() >= next_full:
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
                    _check_stream(stream, store)
                    next_full = clock() + poll_seconds
                    if timeframe is not None:
                        wall = wall_clock()
                        if _awaited_bar_missing(trader, timeframe, wall, settle_seconds) and late_retries < LATE_CANDLE_MAX_RETRIES:
                            late_retries += 1  # l'exchange n'a pas encore publié la bougie : on revient dans 5 s
                            next_full = min(next_full, clock() + LATE_CANDLE_RETRY_SECONDS)
                        else:
                            late_retries = 0
                            next_full = min(next_full, clock() + seconds_until_next_close(timeframe, wall, settle_seconds))
                else:
                    remaining = next_full - clock()
                    if stream is not None:
                        version, quote = stream.wait_next(seen_version, min(remaining, 1.0))
                        if quote is not None and version > seen_version:
                            seen_version = version
                            trader.on_tick(quote)
                    else:
                        sleep(remaining)
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
                failures += 1
                delay = min(poll_seconds * 2**failures, MAX_BACKOFF_SECONDS)
                msg = f"{type(exc).__name__}: {exc} (échec n°{failures}, nouvel essai dans {delay:.0f}s)"
                log.warning(msg)
                store.log_event(pd.Timestamp.now(tz="UTC"), "network_error", msg[:500], commit=True)
                if failures >= RECONNECT_AFTER:
                    feed.reconnect()
                sleep(delay)
                next_full = clock()
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


def _check_stream(stream, store: Store) -> None:
    """Signale un flux coupé depuis plus d'une minute (le filet de sécurité par requêtes prend le relais)."""
    if stream is None or stream.connected_since is not None or not stream.errors:
        return
    silent = stream.seconds_since_update()
    if silent is None or silent > STREAM_DOWN_ALERT_SECONDS:
        detail = f"flux de prix coupé ({stream.last_error}) : stop vérifié par requêtes toutes les minutes en attendant"
        store.log_event(pd.Timestamp.now(tz="UTC"), "stream_error", detail[:500], commit=True)
