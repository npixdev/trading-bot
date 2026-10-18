"""Stratégies de retour à la moyenne."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

import pandas as pd

from bot.strategies.base import Strategy
from bot.strategies.indicators import bollinger, hold_between, rsi


@dataclass(frozen=True)
class BollingerRsiReversion(Strategy):
    """Achète sous la bande de Bollinger basse (si RSI < rsi_max), revend au retour à la moyenne.

    rsi_max = 100 désactive le filtre RSI.
    """

    name: ClassVar[str] = "bollinger"
    grid: ClassVar[dict[str, list[Any]]] = {"window": [20, 50], "k": [1.5, 2.0, 2.5], "rsi_max": [30, 100]}
    window: int = 20
    k: float = 2.0
    rsi_max: float = 30.0
    rsi_period: int = 14

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        lower, mid, _ = bollinger(close, self.window, self.k)
        entry = close < lower
        if self.rsi_max < 100:
            entry &= rsi(close, self.rsi_period) < self.rsi_max
        return hold_between(entry, close > mid)
