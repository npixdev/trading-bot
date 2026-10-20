"""Boucle de trading temps réel (paper, testnet, live) : données, trader, journal, suivi.

Ce package n'envoie jamais d'ordre lui-même : l'exécution passe par un broker (bot.execution).
"""

from bot.realtime.feed import MarketFeed, Quote
from bot.realtime.runner import run_loop
from bot.realtime.store import Store
from bot.realtime.trader import Trader

__all__ = ["MarketFeed", "Quote", "Store", "Trader", "run_loop"]
