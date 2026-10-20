"""Données de marché en temps réel via ccxt (API publique uniquement)."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from bot.data.cache import rows_to_frame
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
    """Bougies clôturées et meilleur bid/ask. Instancie ccxt **sans clés** : lecture seule."""

    def __init__(
        self,
        exchange_name: str,
        symbol: str,
        timeframe: str,
        max_retries: int = 3,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.exchange_name, self.symbol, self.timeframe = exchange_name, symbol, timeframe
        self.max_retries, self.sleep = max_retries, sleep
        self.exchange = create_exchange(exchange_name)

    def reconnect(self) -> None:
        log.warning("Reconnexion à %s", self.exchange_name)
        self.exchange = create_exchange(self.exchange_name)

    def now(self) -> pd.Timestamp:
        return pd.Timestamp.now(tz="UTC")

    def closed_candles(self, n: int) -> pd.DataFrame:
        """Les `n` dernières bougies **clôturées** (la bougie en cours est exclue par le fetcher)."""
        since = self.exchange.milliseconds() - (n + 2) * timeframe_ms(self.timeframe)
        rows = [
            row
            for page in fetch_ohlcv_pages(
                self.exchange, self.symbol, self.timeframe, since, max_retries=self.max_retries, sleep=self.sleep
            )
            for row in page
        ]
        df = rows_to_frame(rows)
        return df[~df.index.duplicated(keep="last")].tail(n)

    def quote(self) -> Quote:
        ticker = call_with_retry(lambda: self.exchange.fetch_ticker(self.symbol), self.max_retries, self.sleep)
        last = float(ticker["last"])
        bid = float(ticker.get("bid") or last)
        ask = float(ticker.get("ask") or last)
        return Quote(bid=bid, ask=ask, last=last)
