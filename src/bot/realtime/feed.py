"""Données de marché en temps réel via ccxt (API publique uniquement)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from bot.data.cache import merge_frames, rows_to_frame
from bot.data.fetcher import call_with_retry, create_exchange, fetch_ohlcv_pages, timeframe_ms

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quote:
    bid: float
    ask: float
    last: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


class MarketFeed:
    """Bougies clôturées et meilleur bid/ask. Instancie ccxt **sans clés** : lecture seule.

    `sandbox=True` lit les prix de l'environnement démo (testnet), qui a son propre carnet d'ordres.
    """

    def __init__(
        self,
        exchange_name: str,
        symbol: str,
        timeframe: str,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        sandbox: bool = False,
    ) -> None:
        self.exchange_name, self.symbol, self.timeframe = exchange_name, symbol, timeframe
        self.max_retries, self.sleep, self.sandbox = max_retries, sleep, sandbox
        self.exchange = create_exchange(exchange_name, sandbox=sandbox)
        self.stream = None  # PriceStream optionnel : prix poussé par WebSocket, plus frais et sans requête
        self._candles: pd.DataFrame | None = None  # bougies closes déjà reçues (téléchargement incrémental)

    def reconnect(self) -> None:
        log.warning("Reconnexion à %s", self.exchange_name)
        self.exchange = create_exchange(self.exchange_name, sandbox=self.sandbox)

    def now(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def closed_candles(self, n: int) -> pd.DataFrame:
        """Les `n` dernières bougies **clôturées** (la bougie en cours est exclue par le fetcher).

        Les bougies déjà reçues sont gardées en mémoire : chaque appel ne demande que les nouvelles
        (1 requête au lieu de 3-4 pour 300 bougies chez OKX). Historique complet re-téléchargé si le
        cache est trop ancien (bot arrêté longtemps) ou trop court.
        """
        tf = timeframe_ms(self.timeframe)
        now = self.exchange.milliseconds()
        cache = self._candles
        if cache is not None and len(cache) >= n:
            last_ms = int(cache.index[-1].value // 1_000_000)
            since = last_ms if now - last_ms < n * tf else None
        else:
            since = None
        if since is None:
            cache, since = None, now - (n + 2) * tf
        rows = [
            row
            for page in fetch_ohlcv_pages(
                self.exchange, self.symbol, self.timeframe, since, max_retries=self.max_retries, sleep=self.sleep
            )
            for row in page
        ]
        merged = merge_frames(cache, rows_to_frame(rows)) if rows else (cache if cache is not None else rows_to_frame([]))
        self._candles = merged.tail(n)
        return self._candles

    def quote(self) -> Quote:
        """Prix du flux WebSocket s'il a moins de 5 s ; sinon une requête classique (filet de sécurité)."""
        if self.stream is not None:
            fresh = self.stream.latest(max_age=5.0)
            if fresh is not None:
                return fresh
        ticker = call_with_retry(lambda: self.exchange.fetch_ticker(self.symbol), self.max_retries, self.sleep)
        last = float(ticker["last"])
        bid = float(ticker.get("bid") or last)
        ask = float(ticker.get("ask") or last)
        return Quote(bid=bid, ask=ask, last=last)
