"""Détection de look-ahead dans une stratégie.

Principe : une stratégie honnête donne la même décision à la bougie t, qu'on lui fournisse
l'historique complet ou seulement l'historique jusqu'à t. Si la décision change quand on
ajoute des bougies *futures*, c'est qu'elle les regarde.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.strategies.base import Strategy


def find_lookahead(strategy: Strategy, df: pd.DataFrame, n_cuts: int = 20) -> list[pd.Timestamp]:
    """Renvoie les bougies où la décision dépend du futur (liste vide = OK)."""
    full = strategy.target_position(df)
    cuts = np.unique(np.linspace(len(df) // 10, len(df) - 1, n_cuts).astype(int))
    bad: list[pd.Timestamp] = []
    for k in cuts:
        partial = strategy.target_position(df.iloc[: k + 1])
        a, b = partial.to_numpy(dtype=float), full.iloc[: k + 1].to_numpy(dtype=float)
        if not np.allclose(a, b, equal_nan=True):
            bad.append(df.index[k])
    return bad
