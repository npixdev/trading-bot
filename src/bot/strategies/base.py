"""Interface Strategy et stratégies de référence."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from typing import Any, ClassVar

import numpy as np
import pandas as pd


class Strategy(ABC):
    """Reçoit un DataFrame OHLCV, renvoie la position cible (0 = cash, 1 = 100 % investi).

    La valeur à l'index t est la décision prise à la **clôture** de la bougie t, avec les seules
    données jusqu'à t inclus. Le moteur l'exécute à l'ouverture de t+1 : la stratégie ne doit
    jamais décaler elle-même son signal.
    Les sous-classes sont des dataclasses : leurs champs sont les paramètres.
    """

    name: ClassVar[str]

    @abstractmethod
    def target_position(self, df: pd.DataFrame) -> pd.Series: ...

    @property
    def params(self) -> dict[str, Any]:
        return asdict(self)  # type: ignore[call-overload]

    def __str__(self) -> str:
        args = ", ".join(f"{k}={v}" for k, v in self.params.items())
        return f"{self.name}({args})"


@dataclass(frozen=True)
class BuyAndHold(Strategy):
    """Toujours investi : la référence à battre."""

    name: ClassVar[str] = "buy_hold"

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(1.0, index=df.index)


@dataclass(frozen=True)
class RandomStrategy(Strategy):
    """Entre et sort au hasard : positions de `avg_hold_bars` bougies en moyenne, investi
    une fraction `exposure` du temps.

    Sert de témoin : une stratégie sans aucune information. Si une « vraie » stratégie ne fait
    pas nettement mieux qu'elle à exposition et fréquence égales, elle n'a probablement rien trouvé.
    """

    name: ClassVar[str] = "random"
    avg_hold_bars: float = 24
    seed: int = 0
    exposure: float = 0.5

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        u = np.random.default_rng(self.seed).random(len(df))
        p_exit = 1.0 / self.avg_hold_bars
        # Durée moyenne hors marché choisie pour que la part du temps investi vaille `exposure`.
        p_entry = min(1.0, p_exit * self.exposure / (1 - self.exposure))
        state = np.empty(len(df))
        current = 0.0
        for i, ui in enumerate(u):
            if ui < (p_exit if current else p_entry):
                current = 1.0 - current
            state[i] = current
        return pd.Series(state, index=df.index)
