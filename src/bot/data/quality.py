"""Contrôles de qualité des données OHLCV."""

from __future__ import annotations

import pandas as pd

from bot.data.fetcher import timeframe_ms


def find_gaps(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Liste les trous : une ligne par interruption, avec début, fin et nombre de bougies manquantes."""
    step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    deltas = df.index.to_series().diff()
    holes = deltas[deltas > step]
    return pd.DataFrame(
        {
            "after": holes.index - holes.values,
            "before": holes.index,
            "missing": (holes / step).astype(int) - 1,
        }
    ).reset_index(drop=True)


def find_invalid_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Bougies incohérentes : high < low, open/close hors de [low, high], volume négatif."""
    bad = (
        (df["high"] < df["low"])
        | (df[["open", "close"]].max(axis=1) > df["high"])
        | (df[["open", "close"]].min(axis=1) < df["low"])
        | (df["volume"] < 0)
    )
    return df[bad]
