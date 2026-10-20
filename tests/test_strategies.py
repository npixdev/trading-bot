from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.backtest.lookahead import find_lookahead
from bot.backtest.validation import param_grid
from bot.strategies import Alternate, BollingerRsiReversion, DonchianBreakout, RandomStrategy, SmaCrossover
from bot.strategies.indicators import bollinger, donchian, hold_between, rsi, sma
from tests.helpers import make_ohlcv


def _random_walk(n: int = 1500, seed: int = 2) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, n)))
    opens = np.concatenate([[100.0], closes[:-1]])
    df = make_ohlcv(list(opens), list(closes))
    df["high"] = df[["open", "close"]].max(axis=1) * (1 + rng.uniform(0, 0.01, n))
    df["low"] = df[["open", "close"]].min(axis=1) * (1 - rng.uniform(0, 0.01, n))
    return df


# --- Indicateurs -------------------------------------------------------------------------


def test_sma() -> None:
    s = pd.Series([1.0, 2, 3, 4, 5])
    assert sma(s, 3).tolist()[2:] == [2.0, 3.0, 4.0]
    assert sma(s, 3).isna().sum() == 2


def test_bollinger_bands() -> None:
    s = pd.Series([10.0, 12, 14])
    lower, mid, upper = bollinger(s, 3, 2.0)
    std = np.std([10, 12, 14])
    assert mid.iloc[-1] == 12
    assert upper.iloc[-1] == pytest.approx(12 + 2 * std)
    assert lower.iloc[-1] == pytest.approx(12 - 2 * std)


def test_rsi_extremes_and_range() -> None:
    up = pd.Series(np.arange(1.0, 40.0))
    down = pd.Series(np.arange(40.0, 1.0, -1))
    assert rsi(up, 14).iloc[-1] == 100
    assert rsi(down, 14).iloc[-1] == pytest.approx(0)
    values = rsi(_random_walk()["close"], 14).dropna()
    assert values.between(0, 100).all()


def test_donchian_excludes_current_bar() -> None:
    high = pd.Series([1.0, 5, 2, 3, 10])
    low = pd.Series([0.5, 4, 1, 2, 9])
    upper, lower = donchian(high, low, 3)
    assert upper.iloc[3] == 5  # max des bougies 0..2, pas de la bougie 3
    assert upper.iloc[4] == 5  # le 10 de la bougie 4 n'est pas inclus
    assert lower.iloc[4] == 1


def test_hold_between_keeps_position_until_exit() -> None:
    entry = pd.Series([False, True, False, False, False, True])
    exit_ = pd.Series([False, False, False, True, False, True])
    assert hold_between(entry, exit_).tolist() == [0, 1, 1, 0, 0, 0]  # la sortie est prioritaire


# --- Stratégies --------------------------------------------------------------------------


def test_sma_crossover_logic() -> None:
    closes = [10.0] * 10 + [20.0] * 10 + [5.0] * 10
    df = make_ohlcv(closes, closes)
    pos = SmaCrossover(fast=2, slow=5).target_position(df)
    assert pos.iloc[:5].eq(0).all()  # pas assez d'historique → cash
    assert pos.iloc[11] == 1  # hausse : moyenne courte (20) au-dessus de la longue (14)
    assert pos.iloc[15] == 0  # les deux valent 20 : pas « au-dessus » → cash
    assert pos.iloc[22] == 0  # baisse : moyenne courte (5) sous la longue (14)


def test_sma_rejects_invalid_params() -> None:
    with pytest.raises(ValueError):
        SmaCrossover(fast=100, slow=50)


def test_donchian_enters_on_breakout_and_exits_on_breakdown() -> None:
    closes = [10.0] * 6 + [12.0, 13.0, 12.5, 12.0, 8.0, 8.0]
    df = make_ohlcv(closes, closes)
    pos = DonchianBreakout(entry=5, exit=2).target_position(df)
    assert pos.iloc[5] == 0
    assert pos.iloc[6] == 1  # 12 > plus haut des 5 bougies précédentes (10)
    assert pos.iloc[8] == 1  # 12,5 n'est pas sous le plus bas des 2 précédentes (12 et 13) → on garde
    assert pos.iloc[9] == 0  # 12 < plus bas des 2 précédentes (13 et 12,5) → sortie
    assert pos.iloc[10] == 0  # pas de nouvelle cassure à la hausse


def test_donchian_rejects_invalid_params() -> None:
    with pytest.raises(ValueError):
        DonchianBreakout(entry=10, exit=20)


def test_bollinger_buys_dip_and_sells_at_mean() -> None:
    closes = [100.0, 101, 99, 100, 101, 99, 100, 101, 99, 100, 80, 85, 95, 105, 104]
    df = make_ohlcv(closes, closes)
    pos = BollingerRsiReversion(window=10, k=2.0, rsi_max=100).target_position(df)
    assert pos.iloc[9] == 0
    assert pos.iloc[10] == 1  # chute à 80 : sous la bande basse
    assert pos.iloc[-1] == 0  # retour au-dessus de la moyenne → sortie


def test_bollinger_rsi_filter_blocks_entries() -> None:
    df = _random_walk()
    loose = BollingerRsiReversion(window=20, k=1.5, rsi_max=100).target_position(df)
    strict = BollingerRsiReversion(window=20, k=1.5, rsi_max=10).target_position(df)
    assert strict.sum() < loose.sum()


@pytest.mark.parametrize("cls", [SmaCrossover, DonchianBreakout, BollingerRsiReversion])
def test_no_lookahead_for_every_grid_combination(cls) -> None:
    df = _random_walk()
    for params in param_grid(cls.grid):
        try:
            strategy = cls(**params)
        except ValueError:
            continue
        assert find_lookahead(strategy, df) == [], f"look-ahead dans {strategy}"


@pytest.mark.parametrize("cls", [SmaCrossover, DonchianBreakout, BollingerRsiReversion])
def test_positions_are_long_only_binary(cls) -> None:
    pos = cls().target_position(_random_walk())
    assert set(pos.unique()) <= {0.0, 1.0}


def test_alternate_flips_by_timestamp_and_is_window_independent() -> None:
    df = _random_walk(40)
    pos = Alternate(period_bars=2).target_position(df)
    assert pos.iloc[:8].tolist() == [0, 0, 1, 1, 0, 0, 1, 1]  # 2021-01-01 00:00 = début d'une période paire
    # En temps réel la fenêtre glisse : la décision d'une bougie ne doit pas en dépendre.
    shifted = Alternate(period_bars=2).target_position(df.iloc[5:])
    pd.testing.assert_series_equal(shifted, pos.iloc[5:])
    assert find_lookahead(Alternate(period_bars=3), df) == []


def test_random_strategy_matches_requested_exposure() -> None:
    df = _random_walk(20_000)
    pos = RandomStrategy(avg_hold_bars=20, seed=1, exposure=0.25).target_position(df)
    assert pos.mean() == pytest.approx(0.25, abs=0.04)
    entries = int(((pos == 1) & (pos.shift(1) == 0)).sum())
    assert pos.sum() / entries == pytest.approx(20, rel=0.15)
