"""Calculs purs de taille de position et de stop."""

from __future__ import annotations

import math


def fixed_weight(fraction: float, max_exposure: float) -> float:
    """Part fixe du capital, plafonnée."""
    return min(fraction, max_exposure)


def atr_weight(risk_per_trade: float, stop_distance_pct: float, max_exposure: float) -> float:
    """Part du capital telle qu'un stop touché coûte `risk_per_trade` du capital.

    poids = risque accepté / distance du stop.
    Ex. : risque 1 %, stop 6 % sous l'entrée → poids 1 % / 6 % ≈ 16,7 % du capital.
    """
    if not math.isfinite(stop_distance_pct) or stop_distance_pct <= 0:
        return 0.0  # volatilité inconnue (début d'historique) : on n'entre pas
    return min(risk_per_trade / stop_distance_pct, max_exposure)


def stop_hit_price(stop: float, bar_open: float, bar_low: float) -> float | None:
    """Prix de déclenchement du stop sur cette bougie, ou None s'il n'est pas touché.

    Si la bougie ouvre déjà sous le stop (gap), on sort à l'ouverture : plus bas que le stop.
    """
    if bar_open <= stop:
        return bar_open
    if bar_low <= stop:
        return stop
    return None
