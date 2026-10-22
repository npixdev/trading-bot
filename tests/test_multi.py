from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.config import BacktestConfig
from bot.report.multi import OOS_START, Sleeve, combine, run_sleeves
from bot.strategies import BuyAndHold
from bot.backtest.engine import run_backtest
from tests.helpers import make_ohlcv

NO_COSTS = BacktestConfig(fee=0.0, slippage=0.0, initial_cash=2_000.0)


def _asset(start: str, n: int, growth: float) -> pd.DataFrame:
    closes = list(100 * (1 + growth) ** np.arange(1, n + 1))
    opens = [100.0] + closes[:-1]
    df = make_ohlcv(opens, closes)
    df.index = pd.date_range(start, periods=n, freq="1D", tz="UTC")
    return df


def test_sleeves_hold_cash_before_eligibility_and_after_delisting() -> None:
    index = pd.date_range(OOS_START, periods=40, freq="1D", tz="UTC")
    early = _asset(f"{OOS_START:%Y-%m-%d}", 40, 0.01)  # disponible tout du long
    late = _asset(f"{index[10]:%Y-%m-%d}", 30, 0.01)  # éligible au jour 10
    delisted = _asset(f"{OOS_START:%Y-%m-%d}", 20, 0.01)  # données arrêtées au jour 19
    sleeves = [
        Sleeve("A", early, early.index[0]),
        Sleeve("B", late, late.index[0]),
        Sleeve("C", delisted, delisted.index[0]),
        Sleeve("D", early, None),  # jamais éligible
    ]
    res = run_sleeves(sleeves, lambda s: BuyAndHold(), BacktestConfig(fee=0.0, slippage=0.0, initial_cash=4_000.0), index)
    assert res.equity.iloc[0] == pytest.approx(4_000)  # 4 parts de 1 000, rien de gagné le 1er jour (achat à l'ouverture J+1)
    # Part B : 1 000 en cash jusqu'au jour 10 ; part D : 1 000 en cash tout du long.
    c_last = run_backtest(delisted, BuyAndHold(), BacktestConfig(fee=0, slippage=0, initial_cash=1_000)).equity.iloc[-1]
    assert res.equity.iloc[-1] > res.equity.iloc[19]
    tail = res.equity.iloc[25:]
    a = run_backtest(early, BuyAndHold(), BacktestConfig(fee=0, slippage=0, initial_cash=1_000)).equity.reindex(tail.index)
    b = run_backtest(late, BuyAndHold(), BacktestConfig(fee=0, slippage=0, initial_cash=1_000)).equity.reindex(tail.index)
    expected = a + b + c_last + 1_000
    pd.testing.assert_series_equal(tail, expected, check_names=False)


def test_combined_position_is_capital_weighted() -> None:
    index = pd.date_range(OOS_START, periods=10, freq="1D", tz="UTC")
    asset = _asset(f"{OOS_START:%Y-%m-%d}", 10, 0.0)
    invested = run_backtest(asset, BuyAndHold(), BacktestConfig(fee=0, slippage=0, initial_cash=1_000))
    res = combine({"A": invested, "B": None}, index, 1_000)
    assert res.position.iloc[-1] == pytest.approx(0.5)  # une part investie sur deux
    assert res.initial_cash == 2_000


def test_random_seeds_are_reproducible_across_processes() -> None:
    import subprocess
    import sys

    code = "import zlib; print(zlib.crc32(b'ETH') % 1000)"
    out = {subprocess.run([sys.executable, "-c", code], capture_output=True, text=True).stdout for _ in range(3)}
    assert len(out) == 1  # contrairement à hash(), identique d'un lancement à l'autre
