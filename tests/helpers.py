from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from bot.strategies.base import Strategy


def make_ohlcv(opens: Sequence[float], closes: Sequence[float] | None = None) -> pd.DataFrame:
    """Bougies 1h à partir de 2021-01-01 ; close = open de la bougie suivante si non fourni."""
    opens = list(opens)
    if closes is None:
        closes = opens[1:] + [opens[-1]]
    idx = pd.date_range("2021-01-01", periods=len(opens), freq="1h", tz="UTC")
    df = pd.DataFrame({"open": opens, "close": list(closes)}, index=idx)
    df["high"] = df[["open", "close"]].max(axis=1)
    df["low"] = df[["open", "close"]].min(axis=1)
    df["volume"] = 1.0
    return df[["open", "high", "low", "close", "volume"]]


class FixedTarget(Strategy):
    """Stratégie de test : renvoie une série de positions donnée."""

    name = "fixed"

    def __init__(self, values: Sequence[float]) -> None:
        self.values = list(values)

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(self.values[: len(df)], index=df.index, dtype=float)

    @property
    def params(self) -> dict[str, object]:
        return {}
