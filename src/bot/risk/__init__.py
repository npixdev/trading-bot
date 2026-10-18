"""Gestion du risque : taille de position, stop-loss, limites et kill switch.

Indépendant du moteur de backtest : le même RiskManager servira en paper trading et en réel.
"""

from bot.risk.manager import RiskEvent, RiskManager
from bot.risk.sizing import atr_weight, fixed_weight, stop_hit_price

__all__ = ["RiskEvent", "RiskManager", "atr_weight", "fixed_weight", "stop_hit_price"]
