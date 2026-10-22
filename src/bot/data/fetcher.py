"""Téléchargement paginé de l'historique OHLCV via ccxt."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol, TypeVar

import ccxt

log = logging.getLogger(__name__)
T = TypeVar("T")

# Avance maximale tolérée de l'horloge du PC sur celle de l'exchange (mesuré : 3,4 s chez l'utilisateur).
CLOCK_MARGIN_MS = 10_000


class OHLCVSource(Protocol):
    """Le sous-ensemble de l'API ccxt dont on a besoin (facilite les tests)."""

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]: ...

    def milliseconds(self) -> int: ...


def create_exchange(name: str, sandbox: bool = False) -> ccxt.Exchange:
    """Instancie un exchange ccxt public (sans clés), avec limitation de débit activée."""
    if name not in ccxt.exchanges:
        raise ValueError(f"Exchange inconnu de ccxt : {name!r}")
    exchange: ccxt.Exchange = getattr(ccxt, name)({"enableRateLimit": True})
    if sandbox:
        exchange.set_sandbox_mode(True)
    return exchange


def timeframe_ms(timeframe: str) -> int:
    return int(ccxt.Exchange.parse_timeframe(timeframe) * 1000)


def call_with_retry(
    fn: Callable[[], T], max_retries: int = 5, sleep: Callable[[float], None] = time.sleep
) -> T:
    """Réessaie sur erreur réseau / rate limit avec attente exponentielle (1s, 2s, 4s…).

    Les autres erreurs (paire inconnue, requête invalide…) remontent immédiatement.
    """
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except ccxt.NetworkError as exc:  # inclut RateLimitExceeded, RequestTimeout, DDoSProtection
            if attempt == max_retries:
                raise
            delay = 2**attempt
            log.warning("Erreur réseau (%s), nouvel essai dans %ss", type(exc).__name__, delay)
            sleep(delay)
    raise AssertionError("inatteignable")


def _fetch_with_retry(
    source: OHLCVSource,
    symbol: str,
    timeframe: str,
    since: int,
    limit: int,
    max_retries: int,
    sleep: Callable[[float], None],
) -> list[list[Any]]:
    return call_with_retry(
        lambda: source.fetch_ohlcv(symbol, timeframe, since=since, limit=limit), max_retries, sleep
    )


def fetch_ohlcv_pages(
    source: OHLCVSource,
    symbol: str,
    timeframe: str,
    since_ms: int,
    until_ms: int | None = None,
    limit: int = 1000,
    max_retries: int = 5,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[list[list[Any]]]:
    """Itère page par page sur les bougies **clôturées** dans [since_ms, until_ms).

    Chaque page est une liste de lignes ccxt [ts_ms, open, high, low, close, volume].
    La bougie en cours (pas encore clôturée) est toujours exclue.

    « Clôturée » ne se fie pas seulement à l'horloge du PC, qui peut avancer de quelques secondes
    sur celle de l'exchange : une bougie est close si l'exchange a déjà ouvert la suivante, ou si sa
    fin est passée depuis plus de `CLOCK_MARGIN_MS` à l'horloge locale.
    """
    tf = timeframe_ms(timeframe)
    now = source.milliseconds()
    end = now if until_ms is None else min(until_ms, now)
    cursor = since_ms

    while cursor < end:
        rows = _fetch_with_retry(source, symbol, timeframe, cursor, limit, max_retries, sleep)
        if not rows:
            break
        newest = int(rows[-1][0])
        page = [
            r for r in rows
            if cursor <= r[0] < end and (r[0] < newest or r[0] + tf <= now - CLOCK_MARGIN_MS)
        ]
        if page:
            yield page
        if newest < cursor:  # l'exchange n'avance plus : on arrête pour éviter une boucle infinie
            break
        # La dernière bougie de la page est ambiguë si rien ne prouve encore sa clôture : on la
        # redemande en tête de la page suivante, qui contiendra peut-être la bougie d'après.
        ambiguous = newest + tf > now - CLOCK_MARGIN_MS
        if ambiguous:
            if len(rows) == 1 or newest == cursor:
                break  # c'est la bougie en cours : rien de plus à récupérer
            cursor = newest
        else:
            cursor = newest + tf
