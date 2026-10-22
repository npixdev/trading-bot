from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import ccxt
import numpy as np
import pandas as pd
import pytest

from bot.config import BacktestConfig, RiskConfig
from bot.execution import PaperBroker
from bot.realtime import MarketFeed, Quote, Store, Trader, run_loop
from bot.realtime.status import render, summarize
from bot.realtime.trader import ConfigMismatch, make_identity
from bot.strategies import Strategy
from tests.conftest import HOUR_MS, FakeExchange
from tests.helpers import make_ohlcv

COSTS = BacktestConfig(fee=0.001, slippage=0.0005, initial_cash=10_000.0)
NO_RISK_EXTRAS = dict(daily_loss_limit=None, kill_switch_drawdown=None)


@dataclass(frozen=True)
class Above(Strategy):
    """Investi quand la clôture dépasse `level`."""

    name: ClassVar[str] = "above"
    level: float = 100.0

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        return (df["close"] > self.level).astype(float)


class FakeFeed:
    """Marché simulé : `advance(i)` place l'horloge juste après la clôture de la bougie i."""

    timeframe = "1h"

    def __init__(self, closes: list[float], spread: float = 0.2) -> None:
        self.candles = make_ohlcv(closes, closes)
        self.i = 0
        self.spread = spread
        self.price_override: float | None = None
        self.reconnects = 0

    def advance(self, i: int, price: float | None = None) -> None:
        self.i, self.price_override = i, price

    def now(self) -> pd.Timestamp:
        return self.candles.index[self.i] + pd.Timedelta(hours=1, seconds=5)

    def closed_candles(self, n: int) -> pd.DataFrame:
        return self.candles.iloc[: self.i + 1].tail(n)

    def quote(self) -> Quote:
        p = self.price_override if self.price_override is not None else float(self.candles["close"].iloc[self.i])
        return Quote(bid=p - self.spread / 2, ask=p + self.spread / 2, last=p)

    def reconnect(self) -> None:
        self.reconnects += 1


def _trader(feed: FakeFeed, db: Path, risk: RiskConfig | None = None, strategy: Strategy | None = None) -> Trader:
    strategy = strategy or Above(100.0)
    identity = make_identity("paper", "fake", "BTC/USDC", feed.timeframe, strategy, risk)
    return Trader(feed, Store(db), strategy, PaperBroker(COSTS), risk, identity, warmup_bars=50)


def _closes() -> list[float]:
    return [99.0] * 5 + [101.0, 102.0, 103.0, 104.0, 105.0] + [95.0] * 5


def test_each_bar_is_processed_once_and_buys_at_ask(tmp_path: Path) -> None:
    feed = FakeFeed(_closes())
    trader = _trader(feed, tmp_path / "p.sqlite")
    feed.advance(4)
    trader.step()
    assert trader.broker.units == 0  # 99 < 100 : pas d'achat
    feed.advance(5)  # clôture à 101 → signal 1 → achat immédiat (= ouverture de la bougie 6)
    trader.step()
    trader.step()  # même bougie : rien de nouveau
    orders = trader.store.table("orders")
    decisions = trader.store.table("decisions")
    assert len(orders) == 1 and len(decisions) == 2
    order = orders.iloc[0]
    assert order["side"] == "buy" and order["reason"] == "signal"
    assert order["price"] == pytest.approx(101.1 * (1 + COSTS.slippage))  # ask + slippage
    assert order["fee"] == pytest.approx(order["qty"] * order["price"] * COSTS.fee)


def test_sells_at_bid_when_signal_drops(tmp_path: Path) -> None:
    feed = FakeFeed(_closes())
    trader = _trader(feed, tmp_path / "p.sqlite")
    for i in range(4, 11):
        feed.advance(i)
        trader.step()
    orders = trader.store.table("orders")
    assert orders["side"].tolist() == ["buy", "sell"]
    assert orders.iloc[1]["price"] == pytest.approx(94.9 * (1 - COSTS.slippage))  # bid − slippage
    assert trader.broker.units == 0


def test_feed_excludes_unclosed_candle() -> None:
    feed = MarketFeed("binance", "BTC/USDT", "1h")
    feed.exchange = FakeExchange(n_candles=120, now_offset_ms=HOUR_MS // 2)  # bougie en cours renvoyée
    candles = feed.closed_candles(50)
    assert len(candles) == 50
    last_close_time = candles.index[-1] + pd.Timedelta(hours=1)
    assert last_close_time <= pd.Timestamp(feed.exchange.now, unit="ms", tz="UTC")


def test_stop_triggers_between_bars_at_bid(tmp_path: Path) -> None:
    risk = RiskConfig(sizing="fixed", stop_atr_mult=None, stop_pct=0.05, **NO_RISK_EXTRAS)
    feed = FakeFeed(_closes())
    trader = _trader(feed, tmp_path / "p.sqlite", risk=risk)
    feed.advance(5)
    trader.step()
    stop = trader.risk.stop
    assert stop == pytest.approx(trader.broker.book.fills[-1]["price"] * 0.95)
    feed.advance(5, price=stop - 1)  # le prix plonge pendant la bougie, avant sa clôture
    trader.step()
    orders = trader.store.table("orders")
    assert orders["reason"].tolist() == ["signal", "stop"]
    assert trader.broker.units == 0
    assert "stop" in trader.store.table("events")["kind"].tolist()


def test_restart_restores_full_state(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    risk = RiskConfig(sizing="fixed", stop_atr_mult=None, stop_pct=0.05, **NO_RISK_EXTRAS)
    feed = FakeFeed(_closes())
    first = _trader(feed, db, risk=risk)
    feed.advance(5)
    first.step()
    cash, units, stop = first.broker.cash, first.broker.units, first.risk.stop
    first.store.close()  # « crash » : le processus disparaît

    second = _trader(feed, db, risk=risk)
    assert (second.broker.cash, second.broker.units, second.risk.stop) == (cash, units, stop)
    assert second.last_bar == feed.candles.index[5]
    second.step()  # même bougie : ne doit pas être retraitée
    assert len(second.store.table("decisions")) == 1
    feed.advance(6)
    second.step()
    assert len(second.store.table("decisions")) == 2


def test_restart_after_downtime_does_not_replay(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    feed = FakeFeed(_closes())
    t = _trader(feed, db)
    feed.advance(3)
    t.step()
    t.store.close()
    feed.advance(9)  # 5 bougies se sont écoulées pendant l'arrêt
    t2 = _trader(feed, db)
    t2.step()
    assert len(t2.store.table("decisions")) == 2  # une seule décision, celle du moment
    events = t2.store.table("events")
    resume = events[events["kind"] == "resume"]["detail"].iloc[0]
    assert resume.startswith("5 bougie(s)")


def test_kill_switch_survives_restart(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    risk = RiskConfig(sizing="fixed", stop_atr_mult=None, daily_loss_limit=None, kill_switch_drawdown=0.05)
    closes = [99.0] * 5 + [101.0, 101.0, 90.0, 120.0, 130.0]
    feed = FakeFeed(closes)
    t = _trader(feed, db, risk=risk)
    for i in range(4, 9):
        feed.advance(i)
        t.step()
    assert t.risk.killed
    t.store.close()
    t2 = _trader(feed, db, risk=risk)
    assert t2.risk.killed
    feed.advance(9)
    t2.step()
    assert t2.broker.units == 0  # signal à 1 mais kill switch actif : aucun rachat
    assert t2.store.table("decisions")["note"].iloc[-1] == "kill switch actif"


def test_refuses_to_resume_with_different_config(tmp_path: Path) -> None:
    db = tmp_path / "p.sqlite"
    feed = FakeFeed(_closes())
    t = _trader(feed, db, strategy=Above(100.0))
    feed.advance(5)
    t.step()
    t.store.close()
    with pytest.raises(ConfigMismatch):
        _trader(feed, db, strategy=Above(50.0))


class FlakyTrader:
    def __init__(self, errors: list[Exception | None]) -> None:
        self.errors = errors
        self.calls = 0

    def step(self) -> None:
        self.calls += 1
        err = self.errors.pop(0) if self.errors else None
        if err is not None:
            raise err


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def test_runner_survives_network_errors_and_reconnects(tmp_path: Path) -> None:
    store = Store(tmp_path / "p.sqlite")
    feed = FakeFeed(_closes())
    errors = [ccxt.NetworkError("coupure"), ccxt.RequestTimeout("lent"), ccxt.ExchangeNotAvailable("maintenance")]
    trader = FlakyTrader(errors + [None] * 10)
    clock = FakeClock()
    sleeps: list[float] = []

    def sleep(s: float) -> None:
        sleeps.append(s)
        clock.sleep(s)

    run_loop(trader, feed, store, poll_seconds=10, duration=200, sleep=sleep, clock=clock)
    events = store.table("events")
    assert (events["kind"] == "network_error").sum() == 3
    assert feed.reconnects == 1  # reconnexion au 3e échec consécutif
    assert sleeps[:3] == [20, 40, 80]  # attente croissante
    assert trader.calls > 3  # la boucle a continué ensuite
    assert events["kind"].iloc[-1] == "run_end"


def test_runner_stops_on_unexpected_error(tmp_path: Path) -> None:
    store = Store(tmp_path / "p.sqlite")
    clock = FakeClock()
    with pytest.raises(ZeroDivisionError):
        run_loop(FlakyTrader([ZeroDivisionError("bug")]), FakeFeed(_closes()), store, 10, 100, clock.sleep, clock)
    assert store.table("events")["kind"].iloc[-1] == "crash"


def test_status_summary(tmp_path: Path) -> None:
    feed = FakeFeed(_closes())
    t = _trader(feed, tmp_path / "p.sqlite")
    for i in range(4, 10):
        feed.advance(i)
        t.step()
    s = summarize(t.store)
    assert s is not None
    assert s["n_orders"] == 1
    assert s["buy_hold_return"] == pytest.approx(105 / 99 - 1)
    assert s["return"] > 0
    assert 0.9 < s["position_weight"] <= 1.0
    text = render(s)
    assert "Buy & hold" in text and "Derniers ordres" in text


def test_paper_module_cannot_send_orders() -> None:
    """Garde-fou de conception : aucun appel d'ordre ni de clé API dans la boucle temps réel."""
    sources = "".join(p.read_text(encoding="utf-8") for p in Path("src/bot/realtime").glob("*.py"))
    for forbidden in ("create_order", "createOrder", "apiKey", "secret", "cancel_order"):
        assert forbidden not in sources
    assert MarketFeed("okx", "BTC/USDC", "1h").exchange.apiKey in (None, "")


def test_step5_journal_is_migrated(tmp_path: Path) -> None:
    """Un journal créé avant l'étape 6 (format plat, sans « mode ») est repris sans perte."""
    db = tmp_path / "old.sqlite"
    feed = FakeFeed(_closes())
    strategy = Above(100.0)
    new_identity = make_identity("paper", "fake", "BTC/USDC", feed.timeframe, strategy, None)
    old_identity = {k: v for k, v in new_identity.items() if k != "mode"}
    store = Store(db)
    store.save_state({
        "identity": old_identity, "cash": 1234.5, "units": 0.5, "fees_paid": 7.0, "entry": None,
        "current_weight": 1.0, "last_bar": feed.candles.index[3].isoformat(), "risk": None, "started": None,
    })
    store.commit()
    store.close()
    t = _trader(feed, db, strategy=strategy)
    assert (t.broker.cash, t.broker.units, t.broker.fees_paid) == (1234.5, 0.5, 7.0)
    assert t.current_weight == 1.0 and t.last_bar == feed.candles.index[3]


def test_risk_state_roundtrip() -> None:
    from bot.risk import RiskManager

    m = RiskManager(RiskConfig())
    t = pd.Timestamp("2026-01-01", tz="UTC")
    m.on_bar_open(t, 10_000)
    m.on_entry(100.0, 2.0)
    m.on_bar_close(t, 9_000)
    restored = RiskManager.from_dict(RiskConfig(), m.to_dict())
    assert restored.to_dict() == m.to_dict()
    assert np.isclose(restored.stop, 94.0)


def test_short_history_is_reported_instead_of_silent(tmp_path: Path) -> None:
    """Vécu : OKX ne garde que ~470 jours de BTC/USDC journalier → le bot restait muet."""
    feed = FakeFeed(_closes())  # 15 bougies seulement
    trader = _trader(feed, tmp_path / "p.sqlite")  # warmup_bars=50
    feed.advance(5)
    trader.step()
    trader.step()
    events = trader.store.table("events")
    assert (events["kind"] == "history_short").sum() == 1  # signalé une fois, pas à chaque tour
    assert "6 bougies disponibles sur 50" in events[events["kind"] == "history_short"]["detail"].iloc[0]
