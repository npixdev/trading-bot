from __future__ import annotations

from typing import Any

import ccxt

HOUR_MS = 3_600_000
T0 = 1_609_459_200_000  # 2021-01-01 00:00 UTC


class FakeExchange:
    """Simule l'API OHLCV de ccxt : bougies 1h de T0 à `now`, pagination par `limit`."""

    def __init__(
        self,
        n_candles: int,
        page_size: int = 100,
        now_offset_ms: int = 0,
        missing: set[int] | None = None,
        failures: int = 0,
    ) -> None:
        self.page_size = page_size
        self.missing = missing or set()
        self.failures_left = failures
        self.calls: list[int] = []
        # `now` tombe au milieu de la bougie suivante si now_offset_ms > 0 → bougie non clôturée.
        self.now = T0 + n_candles * HOUR_MS + now_offset_ms

    def milliseconds(self) -> int:
        return self.now

    def fetch_ohlcv(
        self, symbol: str, timeframe: str, since: int | None = None, limit: int | None = None
    ) -> list[list[Any]]:
        assert timeframe == "1h"
        self.calls.append(since or 0)
        if self.failures_left:
            self.failures_left -= 1
            raise ccxt.RateLimitExceeded("trop de requêtes")
        start = max(since or T0, T0)
        start = T0 + -(-(start - T0) // HOUR_MS) * HOUR_MS  # arrondi à la bougie suivante
        rows: list[list[Any]] = []
        ts = start
        # Comme un vrai exchange, renvoie aussi la bougie en cours (ouverte au plus tard à now).
        while ts <= self.now and len(rows) < min(limit or 1000, self.page_size):
            i = (ts - T0) // HOUR_MS
            if i not in self.missing:
                price = 100.0 + i
                rows.append([ts, price, price + 2, price - 1, price + 1, 10.0])
            ts += HOUR_MS
        return rows
