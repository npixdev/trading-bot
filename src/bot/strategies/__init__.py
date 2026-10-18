"""Stratégies : chacune transforme un DataFrame OHLCV en position cible par bougie."""

from bot.strategies.base import BuyAndHold, RandomStrategy, Strategy
from bot.strategies.mean_reversion import BollingerRsiReversion
from bot.strategies.trend import DonchianBreakout, SmaCrossover

STRATEGIES: dict[str, type[Strategy]] = {
    cls.name: cls
    for cls in (BuyAndHold, RandomStrategy, SmaCrossover, DonchianBreakout, BollingerRsiReversion)
}

__all__ = [
    "STRATEGIES",
    "BollingerRsiReversion",
    "BuyAndHold",
    "DonchianBreakout",
    "RandomStrategy",
    "SmaCrossover",
    "Strategy",
]
