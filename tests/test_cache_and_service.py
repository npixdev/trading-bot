from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from bot.data.cache import cache_path, load_cache, merge_frames, rows_to_frame, save_cache
from bot.data.quality import find_gaps, find_invalid_rows
from bot.data.service import update_ohlcv
from tests.conftest import HOUR_MS, T0, FakeExchange

SINCE = pd.Timestamp(T0, unit="ms", tz="UTC")


def _update(ex: FakeExchange, tmp_path: Path, since: pd.Timestamp = SINCE, **kw) -> pd.DataFrame:
    return update_ohlcv(ex, "fake", "BTC/USDT", "1h", since=since, data_dir=tmp_path, **kw)


def test_cache_path_is_filesystem_safe(tmp_path: Path) -> None:
    p = cache_path(tmp_path, "binance", "BTC/USDT", "1h")
    assert p == tmp_path / "binance" / "BTC-USDT_1h.parquet"


def test_rows_to_frame_uses_utc_open_time_index() -> None:
    df = rows_to_frame([[T0, 1, 2, 0.5, 1.5, 10]])
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert df.index[0] == pd.Timestamp("2021-01-01 00:00", tz="UTC")


def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    df = rows_to_frame([[T0 + i * HOUR_MS, 1, 2, 0.5, 1.5, 10] for i in range(5)])
    path = tmp_path / "x.parquet"
    save_cache(df, path)
    pd.testing.assert_frame_equal(load_cache(path), df, check_freq=False)
    assert not path.with_suffix(".parquet.tmp").exists()


def test_merge_deduplicates_and_sorts() -> None:
    a = rows_to_frame([[T0 + i * HOUR_MS, i, i, i, i, 1] for i in (0, 1, 2)])
    b = rows_to_frame([[T0 + i * HOUR_MS, 99, 99, 99, 99, 1] for i in (3, 2)])
    merged = merge_frames(a, b)
    assert merged.index.is_monotonic_increasing
    assert len(merged) == 4
    assert merged.iloc[2]["close"] == 99  # la donnée la plus récente gagne


def test_full_download_then_cached(tmp_path: Path) -> None:
    df = _update(FakeExchange(n_candles=250), tmp_path)
    assert len(df) == 250
    assert load_cache(cache_path(tmp_path, "fake", "BTC/USDT", "1h")) is not None


def test_resume_only_fetches_new_candles(tmp_path: Path) -> None:
    _update(FakeExchange(n_candles=250, page_size=100), tmp_path)
    later = FakeExchange(n_candles=280, page_size=100)
    df = _update(later, tmp_path)
    assert len(df) == 280
    assert later.calls == [T0 + 250 * HOUR_MS]  # une seule requête, depuis la 251e bougie


def test_extends_backwards_when_since_is_earlier(tmp_path: Path) -> None:
    _update(FakeExchange(n_candles=250), tmp_path, since=SINCE + pd.Timedelta(hours=100))
    df = _update(FakeExchange(n_candles=250), tmp_path)
    assert len(df) == 250
    assert df.index[0] == SINCE


def test_interrupted_download_keeps_saved_pages(tmp_path: Path) -> None:
    class Crashing(FakeExchange):
        def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
            if len(self.calls) == 3:
                raise KeyboardInterrupt  # simule un Ctrl+C pendant la 4e page
            return super().fetch_ohlcv(symbol, timeframe, since, limit)

    with pytest.raises(KeyboardInterrupt):
        _update(Crashing(n_candles=500, page_size=100), tmp_path, save_every=2)
    saved = load_cache(cache_path(tmp_path, "fake", "BTC/USDT", "1h"))
    assert len(saved) == 200  # les 2 premières pages ont été sauvegardées

    resumed = FakeExchange(n_candles=500, page_size=100)
    df = _update(resumed, tmp_path)
    assert len(df) == 500
    assert resumed.calls[0] == T0 + 200 * HOUR_MS


def test_find_gaps() -> None:
    idx = [0, 1, 2, 5, 6, 8]
    df = rows_to_frame([[T0 + i * HOUR_MS, 1, 2, 0.5, 1.5, 1] for i in idx])
    gaps = find_gaps(df, "1h")
    assert gaps["missing"].tolist() == [2, 1]
    assert gaps["after"].iloc[0] == pd.Timestamp(T0 + 2 * HOUR_MS, unit="ms", tz="UTC")


def test_find_invalid_rows() -> None:
    df = rows_to_frame(
        [
            [T0, 1, 2, 0.5, 1.5, 1],  # OK
            [T0 + HOUR_MS, 1, 0.4, 0.5, 1.5, 1],  # high < low
            [T0 + 2 * HOUR_MS, 3, 2, 0.5, 1.5, 1],  # open > high
        ]
    )
    assert len(find_invalid_rows(df)) == 2
