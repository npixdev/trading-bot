"""Agrégation de bougies vers un timeframe plus long (ex. 1h → 4h, 1d)."""

from __future__ import annotations

import pandas as pd

from bot.data.fetcher import timeframe_ms

AGG = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}


def resample_ohlcv(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """Regroupe les bougies par tranches alignées sur minuit UTC.

    La dernière tranche est supprimée si elle n'est pas complète dans les données sources :
    une bougie 1d « en cours » contiendrait une clôture qui n'existe pas encore.
    """
    step = pd.Timedelta(milliseconds=timeframe_ms(timeframe))
    source_step = df.index.to_series().diff().median()
    out = df.resample(step, label="left", closed="left", origin="epoch").agg(AGG)
    out = out.dropna(subset=["open"])
    last_covered = df.index[-1] + source_step
    return out[out.index + step <= last_covered]
