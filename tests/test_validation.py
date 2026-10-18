from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import pandas as pd

from bot.backtest.lookahead import find_lookahead
from bot.backtest.validation import param_grid, train_test_split, walk_forward, walk_forward_windows
from bot.config import BacktestConfig
from bot.strategies import BuyAndHold, RandomStrategy, Strategy
from tests.helpers import make_ohlcv

CONFIG = BacktestConfig(fee=0.001, slippage=0.0005, initial_cash=1000.0)


def _random_walk(n: int, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return make_ohlcv(list(100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))))


def test_train_test_split_is_chronological() -> None:
    df = _random_walk(100)
    train, test = train_test_split(df, 0.7)
    assert len(train) == 70 and len(test) == 30
    assert train.index[-1] < test.index[0]


def test_walk_forward_windows_rolling() -> None:
    idx = pd.date_range("2021-01-01", periods=100, freq="1h", tz="UTC")
    windows = walk_forward_windows(idx, train_bars=40, test_bars=20)
    assert len(windows) == 3
    for w in windows:
        assert w.train_end < w.test_start
        assert idx.get_loc(w.train_end) - idx.get_loc(w.train_start) + 1 == 40
    # Les tests se suivent sans trou ni chevauchement, jusqu'à la fin des données.
    for a, b in zip(windows, windows[1:]):
        assert idx.get_loc(b.test_start) == idx.get_loc(a.test_end) + 1
    assert windows[-1].test_end == idx[-1]


def test_walk_forward_windows_anchored() -> None:
    idx = pd.date_range("2021-01-01", periods=100, freq="1h", tz="UTC")
    windows = walk_forward_windows(idx, train_bars=40, test_bars=20, anchored=True)
    assert all(w.train_start == idx[0] for w in windows)


def test_param_grid() -> None:
    assert param_grid({"a": [1, 2], "b": [3]}) == [{"a": 1, "b": 3}, {"a": 2, "b": 3}]


@dataclass(frozen=True)
class Spy(Strategy):
    """Enregistre la dernière date vue à chaque appel : sert à prouver qu'on ne fuit pas le test."""

    name: ClassVar[str] = "spy"
    calls: ClassVar[list[tuple[int, pd.Timestamp]]] = []
    k: int = 0

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        Spy.calls.append((self.k, df.index[-1]))
        return pd.Series(float(self.k % 2), index=df.index)


def test_optimization_never_sees_test_data() -> None:
    df = _random_walk(300)
    Spy.calls.clear()
    res = walk_forward(df, Spy, {"k": [0, 1, 2]}, CONFIG, train_bars=100, test_bars=50)
    windows = walk_forward_windows(df.index, 100, 50)
    # Chaque fenêtre : 3 appels d'optimisation (train) puis 1 appel d'évaluation (test).
    assert len(Spy.calls) == len(windows) * 4
    for i, w in enumerate(windows):
        optim_calls = Spy.calls[i * 4 : i * 4 + 3]
        assert all(last_seen <= w.train_end for _, last_seen in optim_calls)
    assert len(res.windows) == len(windows)


def test_walk_forward_chains_capital_across_windows() -> None:
    df = _random_walk(300)
    res = walk_forward(df, BuyAndHold, {}, CONFIG, train_bars=100, test_bars=50)
    assert res.equity.index.is_unique and res.equity.index.is_monotonic_increasing
    assert len(res.equity) == 200
    for prev, nxt in zip(res.tests, res.tests[1:]):
        assert nxt.initial_cash == prev.equity.iloc[-1]


def test_lookahead_detector() -> None:
    @dataclass(frozen=True)
    class Cheater(Strategy):
        name: ClassVar[str] = "cheater"

        def target_position(self, df: pd.DataFrame) -> pd.Series:
            return (df["close"].shift(-1) > df["close"]).astype(float)  # regarde la bougie suivante

    @dataclass(frozen=True)
    class Honest(Strategy):
        name: ClassVar[str] = "honest"

        def target_position(self, df: pd.DataFrame) -> pd.Series:
            return (df["close"] > df["close"].rolling(10).mean()).astype(float)

    df = _random_walk(500)
    assert find_lookahead(Cheater(), df)
    assert find_lookahead(Honest(), df) == []
    assert find_lookahead(RandomStrategy(seed=3), df) == []
    assert find_lookahead(BuyAndHold(), df) == []


def test_random_strategy_is_reproducible_and_binary() -> None:
    df = _random_walk(1000)
    a = RandomStrategy(avg_hold_bars=10, seed=7).target_position(df)
    b = RandomStrategy(avg_hold_bars=10, seed=7).target_position(df)
    pd.testing.assert_series_equal(a, b)
    assert set(a.unique()) <= {0.0, 1.0}
    switches = int((a.diff().abs() > 0).sum())
    assert 60 < switches < 140  # ≈ 1000 / 10
