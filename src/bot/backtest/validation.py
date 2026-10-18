"""Séparation train/test et validation walk-forward."""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

import pandas as pd

from bot.backtest.engine import BacktestResult, run_backtest
from bot.config import BacktestConfig
from bot.strategies.base import Strategy


def train_test_split(df: pd.DataFrame, train_frac: float = 0.7) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Coupe dans l'ordre chronologique (jamais de mélange aléatoire sur une série temporelle)."""
    k = int(len(df) * train_frac)
    return df.iloc[:k], df.iloc[k:]


@dataclass(frozen=True)
class Window:
    train_start: pd.Timestamp
    train_end: pd.Timestamp
    test_start: pd.Timestamp
    test_end: pd.Timestamp


def walk_forward_windows(
    index: pd.DatetimeIndex, train_bars: int, test_bars: int, anchored: bool = False
) -> list[Window]:
    """Fenêtres successives : on entraîne sur `train_bars`, on teste sur les `test_bars` suivantes.

    - glissant (défaut) : la fenêtre d'entraînement avance avec le temps (s'adapte aux régimes) ;
    - ancré : l'entraînement commence toujours au début (plus de données, moins réactif).
    Les périodes de test se suivent sans se chevaucher.
    """
    windows: list[Window] = []
    test_start = train_bars
    while test_start < len(index):
        test_end = min(test_start + test_bars, len(index)) - 1
        train_start = 0 if anchored else test_start - train_bars
        windows.append(
            Window(index[train_start], index[test_start - 1], index[test_start], index[test_end])
        )
        test_start += test_bars
    return windows


def param_grid(grid: Mapping[str, Sequence[Any]]) -> list[dict[str, Any]]:
    keys = list(grid)
    return [dict(zip(keys, values)) for values in itertools.product(*grid.values())]


def optimize(
    df: pd.DataFrame,
    factory: Callable[..., Strategy],
    grid: Mapping[str, Sequence[Any]],
    config: BacktestConfig,
    start: pd.Timestamp,
    end: pd.Timestamp,
    metric: str = "sharpe",
) -> tuple[dict[str, Any], float]:
    """Meilleurs paramètres sur [start, end], en ne fournissant aux stratégies que df jusqu'à `end`."""
    train = df.loc[:end]
    best_params: dict[str, Any] = {}
    best_score = -math.inf
    for params in param_grid(grid):
        result = run_backtest(train, factory(**params), config, start=start, end=end)
        score = getattr(result.metrics, metric)
        if not math.isnan(score) and score > best_score:
            best_params, best_score = params, score
    return best_params, best_score


@dataclass
class WalkForwardResult:
    windows: pd.DataFrame  # une ligne par fenêtre : dates, paramètres choisis, score train, résultat test
    equity: pd.Series  # courbe d'equity hors échantillon, fenêtres de test mises bout à bout
    tests: list[BacktestResult]


def walk_forward(
    df: pd.DataFrame,
    factory: Callable[..., Strategy],
    grid: Mapping[str, Sequence[Any]],
    config: BacktestConfig,
    train_bars: int,
    test_bars: int,
    metric: str = "sharpe",
    anchored: bool = False,
) -> WalkForwardResult:
    """Pour chaque fenêtre : optimise sur train, évalue les paramètres retenus sur test."""
    rows: list[dict[str, Any]] = []
    tests: list[BacktestResult] = []
    cash = config.initial_cash
    for w in walk_forward_windows(df.index, train_bars, test_bars, anchored):
        params, train_score = optimize(df, factory, grid, config, w.train_start, w.train_end, metric)
        # Le capital passe d'une fenêtre à l'autre. Chaque test démarre en cash et prend, à sa
        # première bougie, la position décidée à la clôture de la dernière bougie d'entraînement.
        test = run_backtest(
            df, factory(**params), replace(config, initial_cash=cash), start=w.test_start, end=w.test_end
        )
        cash = float(test.equity.iloc[-1])
        tests.append(test)
        rows.append(
            {
                "test_start": w.test_start,
                "test_end": w.test_end,
                "params": params,
                f"train_{metric}": train_score,
                f"test_{metric}": getattr(test.metrics, metric),
                "test_return": test.metrics.total_return,
            }
        )
    equity = pd.concat([t.equity for t in tests]) if tests else pd.Series(dtype=float)
    return WalkForwardResult(pd.DataFrame(rows), equity, tests)
