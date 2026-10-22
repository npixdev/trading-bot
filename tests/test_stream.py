from __future__ import annotations

import asyncio
import time
from pathlib import Path

import pandas as pd
import pytest

from bot.config import RiskConfig
from bot.realtime import MarketFeed, Quote, Store
from bot.realtime.runner import run_loop, seconds_until_next_close
from bot.realtime.stream import PriceStream
from tests.test_paper import FakeClock, FakeFeed, FlakyTrader, _closes, _trader

DAY = 86_400


# --- Calendrier des réveils --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("timeframe", "wall", "expected"),
    [
        ("1d", 10 * DAY + 3600, DAY - 3600 + 3),  # 01:00 → réveil à 00:00:03 le lendemain
        ("1d", 10 * DAY + 1, 2),  # 00:00:01 → encore dans la fenêtre : réveil à 00:00:03
        ("1d", 10 * DAY + 3, DAY),  # pile à 00:00:03 : ce tour vient d'avoir lieu → prochain demain
        ("1h", 10 * DAY + 1800, 1800 + 3),
        ("1m", 10 * DAY + 30, 33),
    ],
)
def test_seconds_until_next_close(timeframe: str, wall: float, expected: float) -> None:
    assert seconds_until_next_close(timeframe, wall, settle_seconds=3) == pytest.approx(expected)


def test_full_step_is_scheduled_right_after_candle_close(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    clock = FakeClock()
    calls: list[float] = []

    class Recorder(FlakyTrader):
        def step(self) -> None:
            calls.append(clock.t)

    # Horloge murale : 23:59:00 ; poll de 300 s ; bougies 1d → réveil à 00:00:03, pas à 00:05:00.
    wall0 = 10 * DAY - 60
    run_loop(Recorder([]), FakeFeed(_closes()), store, poll_seconds=300, duration=100, sleep=clock.sleep,
             clock=clock, timeframe="1d", wall_clock=lambda: wall0 + clock.t)
    assert calls[:2] == [0, pytest.approx(63)]


def test_late_candle_is_retried_quickly(tmp_path: Path) -> None:
    """Clôture passée mais l'exchange n'a pas encore publié la bougie : on revient voir 2 s plus tard."""
    store = Store(tmp_path / "r.sqlite")
    clock = FakeClock()
    wall0 = 10 * DAY + 10  # 00:00:10
    calls: list[float] = []

    class Late(FlakyTrader):
        last_bar = pd.Timestamp((10 * DAY - 2 * DAY), unit="s", tz="UTC")  # dernière bougie traitée : avant-hier

        def step(self) -> None:
            calls.append(clock.t)
            if len(calls) == 3:  # la bougie finit par apparaître
                Late.last_bar = pd.Timestamp(10 * DAY - DAY, unit="s", tz="UTC")

    run_loop(Late([]), FakeFeed(_closes()), store, poll_seconds=300, duration=40, sleep=clock.sleep,
             clock=clock, timeframe="1d", wall_clock=lambda: wall0 + clock.t)
    assert calls[:3] == [0, 2, 4]


# --- Flux et stop-loss ---------------------------------------------------------------------------


class ScriptedStream:
    """Flux simulé : délivre une liste de prix, un toutes les 0,2 s (horloge simulée)."""

    def __init__(self, clock: FakeClock, prices: list[float]) -> None:
        self.clock, self.prices = clock, list(prices)
        self.version, self.connected_since, self.errors, self.last_error = 0, 0.0, 0, None

    def wait_next(self, after_version: int, timeout: float):
        self.clock.sleep(min(timeout, 0.2))
        if not self.prices:
            return self.version, None
        p = self.prices.pop(0)
        self.version += 1
        return self.version, Quote(bid=p - 0.1, ask=p + 0.1, last=p)

    def seconds_since_update(self) -> float:
        return 0.0


def test_stop_is_triggered_by_stream_between_full_steps(tmp_path: Path) -> None:
    risk = RiskConfig(sizing="fixed", stop_atr_mult=None, stop_pct=0.05, daily_loss_limit=None, kill_switch_drawdown=None)
    feed = FakeFeed(_closes())
    trader = _trader(feed, tmp_path / "p.sqlite", risk=risk)
    feed.advance(5)  # clôture à 101 → achat, stop ≈ 96
    clock = FakeClock()
    stream = ScriptedStream(clock, [101.0, 100.0, 99.0, 97.0, 95.0, 94.0])
    run_loop(trader, feed, trader.store, poll_seconds=60, duration=5, sleep=clock.sleep, clock=clock, stream=stream)
    orders = trader.store.table("orders")
    assert orders["reason"].tolist() == ["signal", "stop"]
    assert orders.iloc[1]["price"] == pytest.approx(94.9 * (1 - 0.0005))  # au bid du tick qui a franchi le stop
    assert clock.t < 60  # bien avant le tour complet suivant (60 s)


def test_on_tick_is_a_no_op_without_position(tmp_path: Path) -> None:
    feed = FakeFeed(_closes())
    trader = _trader(feed, tmp_path / "p.sqlite", risk=RiskConfig())
    assert trader.on_tick(Quote(bid=1.0, ask=1.1, last=1.0)) is False
    assert trader.store.table("orders").empty


# --- PriceStream réel (exchange WebSocket simulé) ---------------------------------------------------


class FakeWsExchange:
    def __init__(self, script: list) -> None:
        self.script = script
        self.closed = 0

    async def load_markets(self) -> None:
        return None

    async def watch_ticker(self, symbol: str) -> dict:
        await asyncio.sleep(0.01)
        if not self.script:
            await asyncio.sleep(0.05)
            return {"bid": None, "ask": None}
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return {"bid": item - 1, "ask": item + 1, "last": item}

    async def close(self) -> None:
        self.closed += 1


def test_price_stream_publishes_and_reconnects() -> None:
    connections: list[FakeWsExchange] = []
    scripts = [[100.0, 101.0, ConnectionError("coupure")], [102.0]]

    def factory(name: str, sandbox: bool) -> FakeWsExchange:
        ex = FakeWsExchange(scripts.pop(0) if scripts else [])
        connections.append(ex)
        return ex

    stream = PriceStream("okx", "BTC/USDC", factory=factory)
    stream.start()
    try:
        deadline = time.monotonic() + 8
        while (stream.latest(5) is None or stream.latest(5).last != 102.0) and time.monotonic() < deadline:
            time.sleep(0.05)
        latest = stream.latest(max_age=5)
    finally:
        stream.stop()
        stream.join(timeout=5)
    assert latest is not None and latest.last == 102.0 and latest.bid == 101.0
    assert stream.errors == 1 and len(connections) >= 2  # reconnecté après la coupure
    assert all(c.closed for c in connections[:-1])


def test_quotes_without_bid_ask_are_ignored() -> None:
    stream = PriceStream("okx", "BTC/USDC", factory=lambda n, s: FakeWsExchange([]))
    stream.start()
    time.sleep(0.3)
    stream.stop()
    stream.join(timeout=5)
    assert stream.latest(max_age=60) is None and stream.version == 0


def test_feed_prefers_fresh_stream_price_and_falls_back() -> None:
    feed = MarketFeed("okx", "BTC/USDC", "1m")
    clock = FakeClock()
    stream = PriceStream("okx", "BTC/USDC", factory=lambda n, s: None, clock=clock)
    feed.stream = stream
    stream.publish(Quote(bid=99.0, ask=101.0, last=100.0))
    assert feed.quote().last == 100.0  # aucune requête : prix du flux
    clock.sleep(10)
    assert stream.latest(max_age=5.0) is None  # trop vieux : feed.quote() repasserait par une requête


# --- Optimisations : bougies incrémentales, réveil de veille, anti-veille ---------------------------


def test_feed_downloads_only_new_candles_after_first_call() -> None:
    from tests.conftest import HOUR_MS, FakeExchange

    feed = MarketFeed("binance", "BTC/USDT", "1h")
    ex = FakeExchange(n_candles=400, page_size=100)
    feed.exchange = ex
    first = feed.closed_candles(300)
    first_calls = len(ex.calls)
    assert len(first) == 300 and first_calls >= 3  # historique complet : plusieurs pages
    ex.now += HOUR_MS  # une bougie de plus
    second = feed.closed_candles(300)
    assert len(ex.calls) - first_calls == 1  # une seule requête pour la nouvelle bougie
    assert len(second) == 300 and second.index[-1] == first.index[-1] + pd.Timedelta(hours=1)
    assert second.index.is_monotonic_increasing and second.index.is_unique


def test_feed_refetches_everything_after_long_downtime() -> None:
    from tests.conftest import HOUR_MS, FakeExchange

    feed = MarketFeed("binance", "BTC/USDT", "1h")
    ex = FakeExchange(n_candles=400, page_size=100)
    feed.exchange = ex
    feed.closed_candles(50)
    ex.now += 80 * HOUR_MS  # bot arrêté plus longtemps que la fenêtre
    df = feed.closed_candles(50)
    assert len(df) == 50 and (df.index.to_series().diff().dropna() == pd.Timedelta(hours=1)).all()


class DownTelegram:
    """Telegram injoignable (réveil de veille, Internet coupé) : chaque essai échoue tout de suite."""

    chat_id = "42"

    def __init__(self) -> None:
        self.calls = 0

    def get_updates(self, offset=None, wait=0):
        self.calls += 1
        return None


def test_listener_backs_off_when_telegram_is_unreachable(tmp_path: Path) -> None:
    from bot.monitoring import TelegramListener, TelegramNotifier

    class Down(DownTelegram, TelegramNotifier):
        def __init__(self) -> None:
            TelegramNotifier.__init__(self, "t", "42")
            DownTelegram.__init__(self)

    store = Store(tmp_path / "l.sqlite")
    store.commit()
    tg = Down()
    listener = TelegramListener(tg, store.path, "PAPER", wait=1)
    listener.start()
    time.sleep(1.0)
    listener.stop()
    listener.join(timeout=5)
    assert 1 <= tg.calls <= 2  # vécu avant correction : ~300 essais en 0,5 s


def test_prevent_sleep_returns_a_restore_function() -> None:
    from bot.realtime.power import prevent_sleep

    restore = prevent_sleep()
    assert callable(restore)
    restore()  # ne doit pas lever, sous Windows comme ailleurs
