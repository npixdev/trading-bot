"""Moteur de backtest, métriques et validation hors échantillon."""

from bot.backtest.engine import BacktestResult, run_backtest, simulate
from bot.backtest.lookahead import find_lookahead
from bot.backtest.metrics import Metrics, compute_metrics
from bot.backtest.validation import train_test_split, walk_forward, walk_forward_windows

__all__ = [
    "BacktestResult",
    "Metrics",
    "compute_metrics",
    "find_lookahead",
    "run_backtest",
    "simulate",
    "train_test_split",
    "walk_forward",
    "walk_forward_windows",
]
