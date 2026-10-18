"""Stratégies : chacune transforme un DataFrame OHLCV en position cible par bougie."""

from bot.strategies.base import BuyAndHold, RandomStrategy, Strategy

STRATEGIES: dict[str, type[Strategy]] = {
    BuyAndHold.name: BuyAndHold,
    RandomStrategy.name: RandomStrategy,
}

__all__ = ["STRATEGIES", "BuyAndHold", "RandomStrategy", "Strategy"]
