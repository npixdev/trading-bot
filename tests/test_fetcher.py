from __future__ import annotations

import ccxt
import pytest

from bot.data.fetcher import fetch_ohlcv_pages, timeframe_ms
from tests.conftest import HOUR_MS, T0, FakeExchange


def _all_rows(ex: FakeExchange, **kwargs) -> list[list[float]]:
    return [row for page in fetch_ohlcv_pages(ex, "BTC/USDT", "1h", **kwargs) for row in page]


def test_timeframe_ms() -> None:
    assert timeframe_ms("1m") == 60_000
    assert timeframe_ms("1h") == HOUR_MS
    assert timeframe_ms("1d") == 24 * HOUR_MS


def test_pagination_fetches_everything_without_duplicates() -> None:
    ex = FakeExchange(n_candles=250, page_size=100)
    rows = _all_rows(ex, since_ms=T0, sleep=lambda s: None)
    timestamps = [r[0] for r in rows]
    assert len(rows) == 250
    assert timestamps == sorted(set(timestamps))
    assert len(ex.calls) == 3  # 100 + 100 + 50


def test_unclosed_candle_is_excluded() -> None:
    # now = milieu de la 251e bougie : elle est renvoyée par l'exchange mais pas encore clôturée.
    ex = FakeExchange(n_candles=250, now_offset_ms=HOUR_MS // 2)
    rows = _all_rows(ex, since_ms=T0, sleep=lambda s: None)
    assert len(rows) == 250
    assert rows[-1][0] + HOUR_MS <= ex.now


def test_until_is_exclusive() -> None:
    ex = FakeExchange(n_candles=250)
    rows = _all_rows(ex, since_ms=T0, until_ms=T0 + 120 * HOUR_MS, sleep=lambda s: None)
    assert len(rows) == 120
    assert rows[-1][0] == T0 + 119 * HOUR_MS


def test_gaps_do_not_stop_pagination() -> None:
    ex = FakeExchange(n_candles=250, page_size=100, missing={50, 51, 199})
    rows = _all_rows(ex, since_ms=T0, sleep=lambda s: None)
    assert len(rows) == 247


def test_retries_on_rate_limit_with_backoff() -> None:
    ex = FakeExchange(n_candles=10, failures=3)
    delays: list[float] = []
    rows = _all_rows(ex, since_ms=T0, sleep=delays.append)
    assert len(rows) == 10
    assert delays == [1, 2, 4]


def test_gives_up_after_max_retries() -> None:
    ex = FakeExchange(n_candles=10, failures=10)
    with pytest.raises(ccxt.RateLimitExceeded):
        _all_rows(ex, since_ms=T0, max_retries=2, sleep=lambda s: None)


def test_non_network_errors_are_not_retried() -> None:
    class BadSymbolExchange(FakeExchange):
        def fetch_ohlcv(self, *args, **kwargs):
            self.calls.append(0)
            raise ccxt.BadSymbol("paire inconnue")

    ex = BadSymbolExchange(n_candles=10)
    with pytest.raises(ccxt.BadSymbol):
        _all_rows(ex, since_ms=T0, sleep=lambda s: None)
    assert len(ex.calls) == 1
