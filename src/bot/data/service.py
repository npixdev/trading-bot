"""Mise à jour incrémentale du cache : reprend là où le téléchargement s'est arrêté."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from bot.data.cache import cache_path, load_cache, merge_frames, rows_to_frame, save_cache
from bot.data.fetcher import OHLCVSource, fetch_ohlcv_pages, timeframe_ms

log = logging.getLogger(__name__)


def _to_ms(ts: pd.Timestamp) -> int:
    return int(ts.value // 1_000_000)


def update_ohlcv(
    source: OHLCVSource,
    exchange_name: str,
    symbol: str,
    timeframe: str,
    since: pd.Timestamp,
    data_dir: Path,
    until: pd.Timestamp | None = None,
    save_every: int = 10,
) -> pd.DataFrame:
    """Complète le cache local pour couvrir [since, until) et renvoie toutes les données en cache.

    - Si le cache commence après `since`, on télécharge le début manquant.
    - On télécharge ensuite tout ce qui suit la dernière bougie en cache.
    - Le cache est sauvegardé toutes les `save_every` pages : un arrêt (Ctrl+C, coupure réseau)
      ne fait perdre que les dernières pages, et la commande suivante reprend à partir de là.
    """
    path = cache_path(data_dir, exchange_name, symbol, timeframe)
    df = load_cache(path)
    tf = timeframe_ms(timeframe)
    since_ms = _to_ms(since)
    until_ms = None if until is None else _to_ms(until)

    ranges: list[tuple[int, int | None]] = []
    if df is None or df.empty:
        ranges.append((since_ms, until_ms))
    else:
        first_ms, last_ms = _to_ms(df.index[0]), _to_ms(df.index[-1])
        if since_ms < first_ms:
            ranges.append((since_ms, first_ms))
        ranges.append((last_ms + tf, until_ms))
        log.info("Cache existant : %d bougies, reprise après %s", len(df), df.index[-1])

    for start, end in ranges:
        pending: list[list[float]] = []
        for i, page in enumerate(fetch_ohlcv_pages(source, symbol, timeframe, start, end), 1):
            pending.extend(page)
            if i % save_every == 0:
                df = merge_frames(df, rows_to_frame(pending))
                save_cache(df, path)
                pending = []
                log.info("… %d bougies, jusqu'à %s", len(df), df.index[-1])
        if pending:
            df = merge_frames(df, rows_to_frame(pending))
            save_cache(df, path)

    return df if df is not None else rows_to_frame([])
