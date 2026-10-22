"""Stratégies : chacune transforme un DataFrame OHLCV en position cible par bougie."""

from bot.strategies.base import Alternate, BuyAndHold, RandomStrategy, Strategy
from bot.strategies.mean_reversion import BollingerRsiReversion
from bot.strategies.research import AdaptiveTrend, FixedWeight, LogisticML, TrendFilter, TrendVol, VolTarget
from bot.strategies.trend import DonchianBreakout, SmaCrossover

STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls
    for cls in (
        BuyAndHold, RandomStrategy, SmaCrossover, DonchianBreakout, BollingerRsiReversion, Alternate,
        TrendFilter, VolTarget, TrendVol, LogisticML, FixedWeight, AdaptiveTrend,
    )
}

__all__ = [
    "STRATEGIES",
    "Alternate",
    "BollingerRsiReversion",
    "BuyAndHold",
    "DonchianBreakout",
    "RandomStrategy",
    "SmaCrossover",
    "Strategy",
]
