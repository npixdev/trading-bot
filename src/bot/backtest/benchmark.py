"""Témoin au hasard : la stratégie fait-elle mieux qu'un singe avec la même exposition ?"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.backtest.engine import run_backtest
from bot.config import BacktestConfig
from bot.strategies.base import RandomStrategy


@dataclass(frozen=True)
class RandomBenchmark:
    total_returns: np.ndarray
    sharpes: np.ndarray
    exposure: float
    avg_hold_bars: float

    def percentile(self, total_return: float, sharpe: float) -> tuple[float, float]:
        """Part des stratégies aléatoires qui font **moins bien** (1.0 = meilleure que toutes)."""
        return float((self.total_returns < total_return).mean()), float(
            (np.nan_to_num(self.sharpes, nan=-np.inf) < sharpe).mean()
        )


def random_benchmark(
    df: pd.DataFrame,
    position: pd.Series,
    n_trades: int,
    config: BacktestConfig,
    n_seeds: int = 100,
) -> RandomBenchmark | None:
    """Simule `n_seeds` stratégies aléatoires calées sur l'exposition et la fréquence observées."""
    exposure = float((position > 0).mean())
    if n_trades == 0 or not 0 < exposure < 1:
        return None
    avg_hold = max(1.0, exposure * len(position) / n_trades)
    start, end = position.index[0], position.index[-1]
    returns, sharpes = [], []
    for seed in range(n_seeds):
        strat = RandomStrategy(avg_hold_bars=avg_hold, seed=seed, exposure=exposure)
        m = run_backtest(df, strat, config, start=start, end=end).metrics
        returns.append(m.total_return)
        sharpes.append(m.sharpe)
    return RandomBenchmark(np.array(returns), np.array(sharpes), exposure, avg_hold)
