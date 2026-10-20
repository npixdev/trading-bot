"""Paper trading en temps réel : vrais prix, portefeuille fictif.

Ce module n'a accès à aucune méthode privée de l'exchange (pas de clé API, pas d'envoi d'ordre) :
il ne peut physiquement pas passer d'ordre réel.
"""

from bot.paper.feed import MarketFeed, Quote
from bot.paper.runner import run_loop
from bot.paper.store import Store
from bot.paper.trader import PaperTrader

__all__ = ["MarketFeed", "PaperTrader", "Quote", "Store", "run_loop"]
