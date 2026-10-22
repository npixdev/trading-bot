from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from bot.monitoring.alerts import signal_text
from bot.realtime import Store
from bot.strategies.research import AdaptiveTrend
from tests.helpers import make_ohlcv


def _store_with(tmp_path: Path, info: dict, price: float) -> Store:
    store = Store(tmp_path / "s.sqlite")
    store.save_state({"signal_info": info})
    store.set_meta("last_price", json.dumps({"time": "2026-10-22T10:00:00+00:00", "price": price}))
    store.commit()
    return store


def test_invested_shows_distance_to_sell_level(tmp_path: Path) -> None:
    info = {"kind": "trend", "ready": True, "window": 50, "level": 80_000.0, "close": 84_000.0,
            "invested": True, "next_reselect": "2026-10-22T00:00:00+00:00"}
    text = signal_text(_store_with(tmp_path, info, 84_000.0))
    assert "Investi" in text and "moyenne 50 j" in text and "Vente si" in text
    assert "+5,00%" in text  # 84 000 / 80 000 − 1
    assert "-4,76%" in text  # baisse nécessaire pour atteindre 80 000 depuis 84 000


def test_cash_shows_distance_to_buy_level(tmp_path: Path) -> None:
    info = {"kind": "trend", "ready": True, "window": 100, "level": 90_000.0, "close": 81_000.0,
            "invested": False, "next_reselect": "2026-10-22T00:00:00+00:00"}
    text = signal_text(_store_with(tmp_path, info, 81_000.0))
    assert "En cash" in text and "Achat si" in text and "+11,11%" in text


def test_no_signal_text_for_strategies_without_explanation(tmp_path: Path) -> None:
    store = Store(tmp_path / "s.sqlite")
    store.save_state({"signal_info": None})
    assert signal_text(store) is None


def test_adaptive_trend_explain_is_consistent_with_position() -> None:
    rng = np.random.default_rng(4)
    closes = 100 * np.exp(np.cumsum(rng.normal(0.001, 0.03, 900)))
    df = make_ohlcv(list(np.concatenate([[100.0], closes[:-1]])), list(closes))
    df.index = pd.date_range("2019-01-01", periods=900, freq="1D", tz="UTC")
    strategy = AdaptiveTrend()
    info = strategy.explain(df)
    assert info["ready"] and info["window"] in (50, 100, 150, 200)
    assert info["invested"] == bool(strategy.target_position(df).iloc[-1])
    next_day = pd.Timestamp(info["next_reselect"])
    assert next_day > df.index[-1] and (next_day.value // 86_400_000_000_000) % 91 == 0
