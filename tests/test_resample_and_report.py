from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from bot.backtest.benchmark import random_benchmark
from bot.config import BacktestConfig
from bot.data.resample import resample_ohlcv
from bot.report import run_comparison, verdict, write_report
from bot.strategies import DonchianBreakout, SmaCrossover
from tests.helpers import make_ohlcv

CONFIG = BacktestConfig(fee=0.001, slippage=0.0005, initial_cash=10_000.0)


def _hourly(n_days: int, seed: int = 4) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = n_days * 24
    closes = 100 * np.exp(np.cumsum(rng.normal(0.0001, 0.006, n)))
    opens = np.concatenate([[100.0], closes[:-1]])
    return make_ohlcv(list(opens), list(closes))


def test_resample_aggregates_ohlcv() -> None:
    df = make_ohlcv([1, 2, 3, 4, 5, 6, 7, 8], [2, 3, 4, 5, 6, 7, 8, 9])
    df["high"] = [5, 6, 7, 20, 8, 9, 10, 11]
    out = resample_ohlcv(df, "4h")
    assert len(out) == 2
    first = out.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (1, 20, 1, 5, 4)


def test_resample_drops_incomplete_last_bucket() -> None:
    df = make_ohlcv(list(range(1, 31)))  # 30 h : un jour complet + 6 h
    out = resample_ohlcv(df, "1d")
    assert len(out) == 1
    assert out.index[0] == pd.Timestamp("2021-01-01", tz="UTC")


def test_random_benchmark_matches_exposure() -> None:
    df = _hourly(200)
    position = pd.Series(([1.0] * 30 + [0.0] * 70) * 48, index=df.index)
    bench = random_benchmark(df, position, n_trades=48, config=CONFIG, n_seeds=20)
    assert bench is not None
    assert bench.exposure == pytest.approx(0.3)
    assert bench.avg_hold_bars == pytest.approx(30)
    assert len(bench.total_returns) == 20
    best = bench.percentile(10.0, 99.0)
    worst = bench.percentile(-1.0, -99.0)
    assert best == (1.0, 1.0) and worst == (0.0, 0.0)


def test_random_benchmark_none_without_trades() -> None:
    df = _hourly(10)
    assert random_benchmark(df, pd.Series(0.0, index=df.index), 0, CONFIG) is None


def test_comparison_and_report_end_to_end(tmp_path: Path) -> None:
    df = _hourly(400)
    sections = run_comparison(
        df, ["4h", "1d"], [SmaCrossover, DonchianBreakout], CONFIG, "120D", "60D", n_seeds=5
    )
    assert [s.timeframe for s in sections] == ["4h", "1d"]
    for s in sections:
        bh = s.buy_hold
        for o in s.outcomes:
            # La période hors échantillon est la même pour la stratégie et le buy & hold.
            assert o.wf.equity.index[0] == bh.equity.index[0]
            assert o.wf.equity.index[-1] == bh.equity.index[-1]
            assert isinstance(verdict(o, bh.metrics), str)

    path = write_report(sections, tmp_path / "r.html", "test")
    content = path.read_text(encoding="utf-8")
    assert "__DATA__" not in content and "__SECTIONS__" not in content
    data = json.loads(re.search(r"const DATA = (.*?);\n", content).group(1))
    assert len(data) == 2
    assert set(data[0]["equity"]) == {"buy & hold", "sma", "donchian"}
    assert all(v is None or v <= 0 for v in data[0]["drawdown"]["sma"])
