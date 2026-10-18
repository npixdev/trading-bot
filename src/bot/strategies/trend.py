"""Stratégies de suivi de tendance."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd

from bot.strategies.base import Strategy
from bot.strategies.indicators import donchian, hold_between, sma


@dataclass(frozen=True)
class SmaCrossover(Strategy):
    """Investi quand la moyenne courte est au-dessus de la moyenne longue."""

    name: ClassVar[str] = "sma"
    grid: ClassVar[dict[str, list[Any]]] = {"fast": [10, 20, 50], "slow": [50, 100, 200]}
    fast: int = 20
    slow: int = 100

    def __post_init__(self) -> None:
        if not 0 < self.fast < self.slow:
            raise ValueError(f"il faut 0 < fast < slow (fast={self.fast}, slow={self.slow})")

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        return (sma(close, self.fast) > sma(close, self.slow)).astype(float)


@dataclass(frozen=True)
class DonchianBreakout(Strategy):
    """Achète la cassure du plus haut sur `entry` bougies, sort sous le plus bas sur `exit`."""

    name: ClassVar[str] = "donchian"
    grid: ClassVar[dict[str, list[Any]]] = {"entry": [20, 55, 100], "exit": [10, 20, 50]}
    entry: int = 55
    exit: int = 20

    def __post_init__(self) -> None:
        if not 0 < self.exit <= self.entry:
            raise ValueError(f"il faut 0 < exit <= entry (entry={self.entry}, exit={self.exit})")

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        upper, _ = donchian(df["high"], df["low"], self.entry)
        _, lower = donchian(df["high"], df["low"], self.exit)
        close = df["close"]
        return hold_between(close > upper, close < lower)
