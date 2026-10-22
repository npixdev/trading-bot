"""Flux de prix en temps réel par WebSocket (ccxt.pro) : l'exchange pousse chaque nouveau prix.

Pourquoi : interroger le prix toutes les X secondes (« polling ») est un compromis entre réactivité
du stop-loss et nombre de requêtes. Avec un WebSocket, une seule connexion reste ouverte et
l'exchange envoie chaque changement de prix (≈ 6 par seconde sur OKX) : stop vérifié quasi
instantanément, sans aucune requête supplémentaire.

Le flux tourne dans son propre fil (avec sa boucle asyncio) et se reconnecte tout seul. Il ne fait
que publier le dernier prix : c'est la boucle principale qui décide quoi en faire (pas de partage
d'état de trading entre fils).
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from bot.realtime.feed import Quote

log = logging.getLogger(__name__)

# Le flux le plus réactif qui fournit bid ET ask, mesuré par exchange.
STREAM_METHOD = {"okx": "ticker", "binance": "bids_asks"}


def _default_factory(exchange_name: str, sandbox: bool) -> Any:
    import ccxt.pro as ccxtpro  # import tardif : inutile si le flux est désactivé

    exchange = getattr(ccxtpro, exchange_name)({"enableRateLimit": True})
    if sandbox:
        exchange.set_sandbox_mode(True)
    return exchange


class PriceStream(threading.Thread):
    def __init__(
        self,
        exchange_name: str,
        symbol: str,
        sandbox: bool = False,
        factory: Callable[[str, bool], Any] = _default_factory,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(name="price-stream", daemon=True)
        self.exchange_name, self.symbol, self.sandbox = exchange_name, symbol, sandbox
        self.factory, self.clock = factory, clock
        self.method = STREAM_METHOD.get(exchange_name, "ticker")
        self._cond = threading.Condition()
        self._quote: Quote | None = None
        self._received_at: float | None = None
        self._version = 0  # incrémenté à chaque prix : permet d'attendre « un prix plus récent que X »
        self._stop_flag = threading.Event()
        self.errors = 0
        self.last_error: str | None = None
        self.connected_since: float | None = None

    # --- Côté boucle principale ------------------------------------------------------------

    def latest(self, max_age: float) -> Quote | None:
        """Dernier prix s'il a moins de `max_age` secondes, sinon None (flux muet ou coupé)."""
        with self._cond:
            if self._quote is None or self._received_at is None:
                return None
            return self._quote if self.clock() - self._received_at <= max_age else None

    def wait_next(self, after_version: int, timeout: float) -> tuple[int, Quote | None]:
        """Attend un prix plus récent que `after_version` (au plus `timeout` s)."""
        deadline = self.clock() + max(0.0, timeout)
        with self._cond:
            while self._version <= after_version and not self._stop_flag.is_set():
                remaining = deadline - self.clock()
                if remaining <= 0:
                    return self._version, None
                self._cond.wait(remaining)
            return self._version, self._quote

    @property
    def version(self) -> int:
        with self._cond:
            return self._version

    def seconds_since_update(self) -> float | None:
        with self._cond:
            return None if self._received_at is None else self.clock() - self._received_at

    def stop(self) -> None:
        self._stop_flag.set()
        with self._cond:
            self._cond.notify_all()

    # --- Côté fil du flux --------------------------------------------------------------------

    def publish(self, quote: Quote) -> None:
        with self._cond:
            self._quote, self._received_at = quote, self.clock()
            self._version += 1
            self._cond.notify_all()

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        delay = 1.0
        while not self._stop_flag.is_set():
            exchange = None
            try:
                exchange = self.factory(self.exchange_name, self.sandbox)
                await exchange.load_markets()
                self.connected_since = self.clock()
                log.info("Flux de prix %s %s connecté (%s)", self.exchange_name, self.symbol, self.method)
                while not self._stop_flag.is_set():
                    quote = await self._next_quote(exchange)
                    if quote is not None:
                        self.publish(quote)
                        delay = 1.0
            except Exception as exc:  # noqa: BLE001 — le flux doit survivre à tout (réseau, maintenance…)
                self.errors += 1
                self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                self.connected_since = None
                log.warning("Flux de prix interrompu (%s), reconnexion dans %.0f s", self.last_error, delay)
            finally:
                if exchange is not None:
                    try:
                        await exchange.close()
                    except Exception:  # noqa: BLE001
                        pass
            if self._stop_flag.is_set():
                break
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _next_quote(self, exchange: Any) -> Quote | None:
        if self.method == "bids_asks":
            data = await exchange.watch_bids_asks([self.symbol])
            t = data.get(self.symbol) or {}
        else:
            t = await exchange.watch_ticker(self.symbol)
        bid, ask = t.get("bid"), t.get("ask")
        if not bid or not ask:
            return None  # un prix sans bid/ask ne permet pas de vérifier un stop au prix de vente réel
        last = t.get("last") or (float(bid) + float(ask)) / 2
        return Quote(bid=float(bid), ask=float(ask), last=float(last))
