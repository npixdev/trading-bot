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


class SkewedExchange(FakeExchange):
    """L'horloge du PC avance de `skew_ms` : à « minuit » local, l'exchange n'a pas encore clôturé la bougie."""

    def __init__(self, n_candles: int, skew_ms: int) -> None:
        super().__init__(n_candles)
        self.server_now = self.now - skew_ms  # l'exchange vit légèrement dans le passé
        self.now = self.now + 2_000  # le PC se réveille 2 s après « sa » clôture

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        rows = super().fetch_ohlcv(symbol, timeframe, since, limit)
        return [r for r in rows if r[0] <= self.server_now]  # l'exchange n'a pas encore ouvert la suivante


def test_clock_ahead_of_exchange_does_not_leak_unclosed_candle() -> None:
    """PC en avance de 3,4 s (mesuré) : la bougie que le PC croit close est encore ouverte côté exchange."""
    ex = SkewedExchange(n_candles=50, skew_ms=3_400)
    rows = _all_rows(ex, since_ms=T0, sleep=lambda s: None)
    assert len(rows) == 49  # la 50e (ouverte côté exchange) est écartée
    assert rows[-1][0] == T0 + 48 * HOUR_MS


def test_candle_is_closed_as_soon_as_exchange_opens_the_next() -> None:
    ex = FakeExchange(n_candles=50)  # now = clôture exacte de la 50e ; l'exchange a ouvert la 51e
    rows = _all_rows(ex, since_ms=T0, sleep=lambda s: None)
    assert len(rows) == 50  # pas besoin d'attendre la marge de sécurité


def test_last_closed_candle_on_a_page_boundary_is_not_lost() -> None:
    """200 bougies, pages de 100 : la 200e termine la page 2, la preuve de sa clôture est en page 3."""
    ex = FakeExchange(n_candles=200, page_size=100)
    rows = _all_rows(ex, since_ms=T0, sleep=lambda s: None)
    timestamps = [r[0] for r in rows]
    assert len(rows) == 200 and timestamps == sorted(set(timestamps))


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
