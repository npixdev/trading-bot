from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from bot.backtest.engine import run_backtest, simulate
from bot.config import BacktestConfig, RiskConfig
from bot.risk import RiskManager, atr_weight, fixed_weight, stop_hit_price
from tests.helpers import FixedTarget, make_ohlcv

NO_COSTS = BacktestConfig(fee=0.0, slippage=0.0, initial_cash=10_000.0)
COSTS = BacktestConfig(fee=0.001, slippage=0.0005, initial_cash=10_000.0)

# Garde-fous désactivés sauf celui qu'on teste.
OFF = dict(sizing="fixed", stop_atr_mult=None, daily_loss_limit=None, kill_switch_drawdown=None)


def _risk(**overrides) -> RiskConfig:
    return RiskConfig(**{**OFF, **overrides})


def _ohlc(opens, highs, lows, closes, freq: str = "1h") -> pd.DataFrame:
    idx = pd.date_range("2021-01-01", periods=len(opens), freq=freq, tz="UTC")
    return pd.DataFrame(
        {"open": opens, "high": highs, "low": lows, "close": closes, "volume": 1.0}, index=idx
    )


# --- Fonctions de sizing et de stop --------------------------------------------------------


def test_fixed_weight_is_capped() -> None:
    assert fixed_weight(0.5, 1.0) == 0.5
    assert fixed_weight(1.0, 0.3) == 0.3


def test_atr_weight_example() -> None:
    # Risque 1 %, stop 6 % sous l'entrée → 16,7 % du capital : un stop touché coûte 1 %.
    assert atr_weight(0.01, 0.06, 1.0) == pytest.approx(1 / 6)
    assert atr_weight(0.01, 0.005, 1.0) == 1.0  # stop très serré → plafonné à 100 %, jamais de levier
    assert atr_weight(0.01, math.nan, 1.0) == 0.0  # ATR inconnu → on n'entre pas


def test_stop_hit_price() -> None:
    assert stop_hit_price(90, bar_open=100, bar_low=95) is None
    assert stop_hit_price(90, bar_open=100, bar_low=85) == 90
    assert stop_hit_price(90, bar_open=80, bar_low=75) == 80  # gap : sortie à l'ouverture, sous le stop


# --- Validation de la configuration ---------------------------------------------------------


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_exposure": 1.5},  # levier
        {"max_exposure": 0},
        {"sizing": "martingale"},
        {"stop_pct": 0.05, "stop_atr_mult": 3.0},
        {"sizing": "atr", "stop_atr_mult": None, "stop_pct": None},
        {"daily_loss_limit": 1.5},
        {"risk_per_trade": 0.0},
    ],
)
def test_invalid_risk_config_is_rejected(kwargs) -> None:
    with pytest.raises(ValueError):
        RiskConfig(**kwargs)


def test_leverage_is_refused_with_explicit_message() -> None:
    with pytest.raises(ValueError, match="levier"):
        RiskConfig(max_exposure=2.0)


# --- RiskManager seul -----------------------------------------------------------------------


def test_manager_atr_sizing_and_locked_weight() -> None:
    m = RiskManager(RiskConfig(sizing="atr", risk_per_trade=0.01, stop_atr_mult=3.0))
    t = pd.Timestamp("2021-01-01", tz="UTC")
    m.on_bar_open(t, 10_000)
    # Prix 100, ATR 2 → stop 6 sous l'entrée (6 %) → poids 1/6.
    w = m.target_weight(1.0, 0.0, price=100, atr=2.0)
    assert w == pytest.approx(1 / 6)
    m.on_entry(100, atr=2.0)
    assert m.stop == pytest.approx(94)
    # En position : la taille ne bouge plus, même si l'ATR change.
    assert m.target_weight(1.0, w, price=110, atr=10.0) == w


def test_manager_waits_for_flat_signal_after_stop() -> None:
    m = RiskManager(_risk(stop_pct=0.1))
    t = pd.Timestamp("2021-01-01", tz="UTC")
    m.on_bar_open(t, 10_000)
    m.target_weight(1.0, 0.0, 100, math.nan)
    m.on_entry(100, math.nan)
    assert m.check_stop(t, 95, 85) == 90
    assert m.target_weight(1.0, 0.0, 85, math.nan) == 0.0  # signal toujours à 1 : pas de rachat
    assert m.target_weight(0.0, 0.0, 85, math.nan) == 0.0  # le signal repasse à 0…
    assert m.target_weight(1.0, 0.0, 85, math.nan) == 1.0  # … puis à 1 : nouvelle entrée autorisée


def test_manager_kill_switch_and_manual_reset() -> None:
    m = RiskManager(_risk(kill_switch_drawdown=0.3))
    t = pd.Timestamp("2021-01-01", tz="UTC")
    m.on_bar_open(t, 10_000)
    m.on_bar_close(t, 10_000)
    m.on_bar_close(t, 6_900)  # −31 %
    assert m.killed and m.events[-1].kind == "kill_switch"
    assert m.target_weight(1.0, 0.0, 100, math.nan) == 0.0
    m.reset_kill_switch()
    assert m.target_weight(1.0, 0.0, 100, math.nan) == 1.0


# --- Garde-fous dans le moteur : ce que chacun évite -----------------------------------------


def test_stop_loss_caps_a_slow_crash() -> None:
    """Sans stop : −50 %. Avec un stop à 10 % : environ −10 %."""
    prices = list(np.linspace(100, 50, 30))
    df = make_ohlcv(prices)
    target = FixedTarget([1] * 30)
    without = run_backtest(df, target, NO_COSTS)
    with_stop = run_backtest(df, target, NO_COSTS, risk=_risk(stop_pct=0.10))
    assert without.equity.iloc[-1] / 10_000 - 1 == pytest.approx(50 / prices[1] - 1)
    assert with_stop.equity.iloc[-1] == pytest.approx(10_000 * 0.9)  # sortie exactement au stop
    assert with_stop.events["kind"].tolist() == ["stop"]


def test_stop_gap_exits_at_open_below_stop() -> None:
    """Un gap baissier traverse le stop : la perte dépasse ce qui était prévu."""
    df = _ohlc([100, 100, 70, 70], [100, 101, 71, 71], [100, 99, 65, 69], [100, 100, 70, 70])
    res = run_backtest(df, FixedTarget([1, 1, 1, 1]), NO_COSTS, risk=_risk(stop_pct=0.10))
    sell = res.fills[res.fills["side"] == "sell"].iloc[0]
    assert sell["price"] == 70  # stop à 90, mais l'ouverture est déjà à 70
    assert res.equity.iloc[-1] == pytest.approx(7_000)
    assert "gap" in res.events["detail"].iloc[0]


def test_no_reentry_after_stop_while_signal_stays_on() -> None:
    prices = [100, 100, 85, 86, 87, 88, 89, 90]
    df = _ohlc(prices, [p + 1 for p in prices], [p - 1 for p in prices], prices)
    res = run_backtest(df, FixedTarget([1, 1, 1, 1, 1, 0, 1, 1]), NO_COSTS, risk=_risk(stop_pct=0.10))
    buys = res.fills[res.fills["side"] == "buy"]
    # Entrée bougie 1, stop bougie 2 ; signal à 0 en bougie 5 → nouvel achat seulement en bougie 7.
    assert buys["time"].tolist() == [df.index[1], df.index[7]]


def test_atr_sizing_loses_about_risk_per_trade_when_stopped() -> None:
    """Le stop touché coûte ≈ 1 % du capital, quelle que soit la volatilité."""
    rng = np.random.default_rng(0)
    n = 60
    closes = 100 + np.cumsum(rng.normal(0, 1, n))
    closes[40:] = closes[39] - np.arange(1, n - 39) * 3  # chute nette à la fin
    opens = np.concatenate([[closes[0]], closes[:-1]])
    df = _ohlc(opens, np.maximum(opens, closes) + 0.5, np.minimum(opens, closes) - 0.5, closes)
    target = FixedTarget([0] * 35 + [1] * (n - 35))
    res = run_backtest(df, target, NO_COSTS, risk=_risk(sizing="atr", risk_per_trade=0.01, stop_atr_mult=3.0))
    assert res.events["kind"].tolist() == ["stop"]
    loss = res.trades["return"].iloc[0]
    assert loss == pytest.approx(-0.01, abs=0.002)
    assert res.position.max() < 1.0  # taille réduite, pas tout le capital


def test_daily_loss_limit_flattens_and_resumes_next_day() -> None:
    """Un krach horaire : on coupe à −5 % au lieu de subir toute la journée."""
    n = 48
    prices = [100.0] * n
    for i in range(3, 24):
        prices[i] = 100 - 2 * (i - 2)  # −2 par heure à partir de 03:00
    df = make_ohlcv(prices)
    res = run_backtest(df, FixedTarget([1] * n), NO_COSTS, risk=_risk(daily_loss_limit=0.05))
    ev = res.events
    assert ev["kind"].tolist() == ["daily_limit"]
    halt_time = ev["time"].iloc[0]
    sell = res.fills[res.fills["side"] == "sell"].iloc[0]
    assert sell["time"] == halt_time + pd.Timedelta(hours=1)  # vente à l'ouverture suivante
    # Pas de rachat avant minuit UTC, puis reprise le lendemain.
    buys = res.fills[res.fills["side"] == "buy"]["time"]
    assert buys.iloc[1] == pd.Timestamp("2021-01-02 00:00", tz="UTC")
    without = run_backtest(df, FixedTarget([1] * n), NO_COSTS)
    assert res.equity.loc["2021-01-01"].min() > without.equity.loc["2021-01-01"].min()


def test_kill_switch_stops_trading_for_good() -> None:
    """Drawdown de 30 % : tout vendre et ne plus jamais racheter, même si le prix remonte."""
    prices = [100, 100, 90, 80, 69, 60, 80, 120, 150]
    df = make_ohlcv(prices)
    res = run_backtest(df, FixedTarget([1] * len(prices)), NO_COSTS, risk=_risk(kill_switch_drawdown=0.30))
    assert res.events["kind"].tolist() == ["kill_switch"]
    assert len(res.fills) == 2  # un achat, une vente, plus rien ensuite
    assert res.position.iloc[-3:].eq(0).all()
    assert res.equity.iloc[-1] == pytest.approx(res.equity.iloc[-3])


def test_max_exposure_caps_position() -> None:
    df = make_ohlcv([100, 100, 200, 200])
    res = run_backtest(df, FixedTarget([1, 1, 1, 1]), NO_COSTS, risk=_risk(max_exposure=0.5))
    assert res.position.max() == 0.5
    assert res.equity.iloc[-1] == pytest.approx(5_000 + 5_000 * 2)


def test_max_exposure_is_enforced_continuously() -> None:
    """Le BTC triple : sans réduction, 50 % investis deviendraient 75 % du portefeuille."""
    prices = [100, 100, 150, 200, 300, 300, 100]
    df = make_ohlcv(prices)
    res = run_backtest(df, FixedTarget([1] * len(prices)), NO_COSTS, risk=_risk(max_exposure=0.5))
    held = res.fills.assign(signed=lambda f: f["qty"].where(f["side"] == "buy", -f["qty"]))["signed"].cumsum()
    actual = []
    for t, o in zip(df.index, df["open"]):
        units = held[res.fills["time"] <= t].iloc[-1] if (res.fills["time"] <= t).any() else 0.0
        eq = res.equity.shift(1).fillna(10_000).loc[t]
        actual.append(units * o / eq)
    assert max(actual) <= 0.5 + 0.05 + 1e-9
    assert (res.events["kind"] == "exposure").sum() >= 2
    assert (res.fills["side"] == "sell").sum() >= 2  # surplus revendu à plusieurs reprises


def test_risk_uses_previous_bar_atr_only() -> None:
    """Pas de look-ahead : modifier la bougie d'entrée ne change pas la taille de position."""
    rng = np.random.default_rng(3)
    closes = 100 + np.cumsum(rng.normal(0, 1, 40))
    opens = np.concatenate([[100.0], closes[:-1]])
    df = _ohlc(opens, np.maximum(opens, closes) + 1, np.minimum(opens, closes) - 1, closes)
    target = pd.Series([0.0] * 29 + [1.0] * 11, index=df.index)  # entrée à l'ouverture de la bougie 30
    altered = df.copy()
    altered.iloc[30, altered.columns.get_loc("high")] += 50  # bougie d'entrée beaucoup plus agitée
    cfg = _risk(sizing="atr", risk_per_trade=0.01, stop_atr_mult=3.0)
    a = simulate(df, target, NO_COSTS, risk=cfg)
    b = simulate(altered, target, NO_COSTS, risk=cfg)
    assert a.position.iloc[30] == b.position.iloc[30] > 0


def test_without_risk_config_engine_is_unchanged() -> None:
    df = make_ohlcv([100, 90, 80, 120, 130])
    a = run_backtest(df, FixedTarget([1] * 5), COSTS)
    b = run_backtest(df, FixedTarget([1] * 5), COSTS, risk=None)
    pd.testing.assert_series_equal(a.equity, b.equity)
    assert a.events.empty
