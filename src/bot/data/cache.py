"""Cache local OHLCV au format Parquet (un fichier par exchange/symbole/timeframe)."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd

OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


def cache_path(data_dir: Path, exchange: str, symbol: str, timeframe: str) -> Path:
    safe_symbol = symbol.replace("/", "-").replace(":", "_")
    return data_dir / exchange / f"{safe_symbol}_{timeframe}.parquet"


def rows_to_frame(rows: list[list[float]]) -> pd.DataFrame:
    """Convertit les lignes ccxt [ts_ms, o, h, l, c, v] en DataFrame indexé en UTC."""
    df = pd.DataFrame(rows, columns=["timestamp", *OHLCV_COLUMNS])
    df.index = pd.to_datetime(df.pop("timestamp").astype("int64"), unit="ms", utc=True)
    df.index.name = "timestamp"
    return df.astype("float64")


def merge_frames(old: pd.DataFrame | None, new: pd.DataFrame) -> pd.DataFrame:
    """Fusionne en dédupliquant par timestamp (la donnée la plus récente gagne)."""
    if old is None or old.empty:
        merged = new
    else:
        merged = pd.concat([old, new])
    merged = merged[~merged.index.duplicated(keep="last")]
    return merged.sort_index()


def load_cache(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    return pd.read_parquet(path)


def save_cache(df: pd.DataFrame, path: Path) -> None:
    """Écriture atomique : un crash pendant l'écriture ne corrompt pas le cache existant."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_parquet(tmp)
    os.replace(tmp, path)
