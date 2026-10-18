from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bot.backtest.metrics import compute_metrics, max_drawdown, periods_per_year

EMPTY_TRADES = pd.DataFrame(columns=["return", "open"])


def _daily(values: list[float]) -> pd.Series:
    return pd.Series(values, index=pd.date_range("2021-01-01", periods=len(values), freq="1D", tz="UTC"))


def test_periods_per_year() -> None:
    idx = pd.date_range("2021-01-01", periods=100, freq="1h", tz="UTC")
    assert periods_per_year(idx) == pytest.approx(8766)


def test_max_drawdown() -> None:
    assert max_drawdown(pd.Series([100, 120, 60, 90, 130, 117])) == pytest.approx(-0.5)


def test_total_return_and_cagr() -> None:
    # Doublement en exactement deux ans → CAGR = √2 − 1 ≈ 41,4 %.
    n = int(round(2 * 365.25))
    equity = _daily(list(np.geomspace(1000 * 2 ** (1 / n), 2000, n)))
    m = compute_metrics(equity, 1000, EMPTY_TRADES, equity * 0 + 1)
    assert m.total_return == pytest.approx(1.0)
    assert m.cagr == pytest.approx(math.sqrt(2) - 1, rel=1e-3)
    assert m.max_drawdown == 0.0


def test_sharpe_and_sortino() -> None:
    rets = np.array([0.01, -0.005] * 50)
    equity = _daily(list(1000 * np.cumprod(1 + rets)))
    m = compute_metrics(equity, 1000, EMPTY_TRADES, equity * 0 + 1)
    s = pd.Series(rets)
    assert m.sharpe == pytest.approx(s.mean() / s.std() * math.sqrt(365.25))
    downside = math.sqrt(np.mean(np.minimum(rets, 0) ** 2))
    assert m.sortino == pytest.approx(s.mean() / downside * math.sqrt(365.25))
    assert m.sortino > m.sharpe  # les hausses ne sont pas pénalisées


def test_degenerate_cases_do_not_crash() -> None:
    equity = _daily([1000.0] * 10)
    m = compute_metrics(equity, 1000, EMPTY_TRADES, equity * 0)
    assert math.isnan(m.sharpe) and math.isnan(m.sortino) and math.isnan(m.win_rate)
    assert m.exposure == 0.0


def test_win_rate_ignores_open_trade() -> None:
    trades = pd.DataFrame({"return": [0.1, -0.05, 0.02, 0.3], "open": [False, False, False, True]})
    equity = _daily([1000.0, 1010.0])
    m = compute_metrics(equity, 1000, trades, equity * 0 + 1)
    assert m.n_trades == 4
    assert m.win_rate == pytest.approx(2 / 3)
