from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from bot.backtest.dca import simulate_dca
from bot.backtest.engine import run_backtest
from bot.backtest.lookahead import find_lookahead
from bot.backtest.metrics import Metrics
from bot.backtest.validation import param_grid
from bot.config import BacktestConfig
from bot.report.research import evaluate
from bot.strategies.research import (
    FixedWeight,
    LogisticML,
    TrendFilter,
    TrendVol,
    VolTarget,
    fit_logistic,
    ml_features,
    predict_proba,
    vol_target_weight,
)
from tests.helpers import make_ohlcv

NO_COSTS = BacktestConfig(fee=0.0, slippage=0.0, initial_cash=10_000.0)
COSTS = BacktestConfig(fee=0.001, slippage=0.0005, initial_cash=10_000.0)


def _daily_walk(n: int = 700, seed: int = 5, drift: float = 0.0005, vol: float = 0.03) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 * np.exp(np.cumsum(rng.normal(drift, vol, n)))
    opens = np.concatenate([[100.0], closes[:-1]])
    df = make_ohlcv(list(opens), list(closes))
    df.index = pd.date_range("2018-01-01", periods=n, freq="1D", tz="UTC")
    df["volume"] = rng.uniform(100, 200, n)
    return df


# --- Pas de look-ahead, pour chaque combinaison de chaque grille --------------------------------


@pytest.mark.parametrize("cls", [TrendFilter, VolTarget, TrendVol, LogisticML])
def test_no_lookahead_for_every_research_combination(cls) -> None:
    df = _daily_walk()
    for params in param_grid(cls.grid):
        strategy = cls(**params)
        assert find_lookahead(strategy, df, n_cuts=12) == [], f"look-ahead dans {strategy}"


def test_ml_features_use_only_the_past() -> None:
    df = _daily_walk(300)
    full = ml_features(df)
    cut = ml_features(df.iloc[:200])
    pd.testing.assert_frame_equal(full.iloc[:200], cut)


# --- Régression logistique --------------------------------------------------------------------------


def test_logistic_regression_learns_a_clear_rule() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(size=(2000, 3))
    y = (x[:, 0] - 0.5 * x[:, 1] + rng.normal(scale=0.3, size=2000) > 0).astype(float)
    beta = fit_logistic(x, y, l2=0.1)
    accuracy = ((predict_proba(beta, x) > 0.5) == y).mean()
    assert accuracy > 0.9
    assert beta[1] > 0 and beta[2] < 0 and abs(beta[3]) < abs(beta[1]) / 5  # la 3e variable est du bruit


def test_l2_regularization_shrinks_coefficients() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(500, 2))
    y = (x[:, 0] > 0).astype(float)
    weak = fit_logistic(x, y, l2=0.01)
    strong = fit_logistic(x, y, l2=100.0)
    assert abs(strong[1]) < abs(weak[1])


def test_ml_strategy_finds_an_edge_when_one_exists() -> None:
    """Contrôle positif : si demain ressemble à aujourd'hui (momentum fort), le modèle doit le trouver.

    Sans ce test, un échec sur le BTC ne prouverait rien (le modèle pourrait être simplement cassé).
    """
    rng = np.random.default_rng(2)
    n = 900
    ret = np.empty(n)
    ret[0] = 0.01
    for i in range(1, n):  # le signe du jour suit celui de la veille 75 % du temps
        ret[i] = abs(rng.normal(0.01, 0.005)) * (np.sign(ret[i - 1]) if rng.random() < 0.75 else -np.sign(ret[i - 1]))
    closes = 100 * np.exp(np.cumsum(ret))
    df = make_ohlcv(list(np.concatenate([[100.0], closes[:-1]])), list(closes))
    df.index = pd.date_range("2018-01-01", periods=n, freq="1D", tz="UTC")
    position = LogisticML(threshold=0.5, l2=1.0).target_position(df)
    up_tomorrow = (df["close"].shift(-1) > df["close"]).astype(float)
    active = slice(300, n - 1)
    accuracy = (position.iloc[active] == up_tomorrow.iloc[active]).mean()
    # Règle idéale ≈ 72 % ; avec 11 variables dont 10 inutiles ici, le modèle surapprend un peu (≈ 64 %).
    assert accuracy > 0.60  # nettement au-dessus de 50 % (pile ou face)

    # Contrôle de l'implémentation : avec la seule variable utile, on retrouve le score idéal.
    x = ml_features(df)[["ret_1j"]].to_numpy()
    y = up_tomorrow.to_numpy()
    ok = ~np.isnan(x).any(axis=1)
    x_train, y_train = x[:300][ok[:300]], y[:300][ok[:300]]
    mean, std = x_train.mean(axis=0), x_train.std(axis=0)  # standardisation, comme dans la stratégie
    beta = fit_logistic((x_train - mean) / std, y_train, l2=1.0)
    single = (predict_proba(beta, (x[300 : n - 1] - mean) / std) > 0.5) == y[300 : n - 1]
    assert single.mean() > 0.70


def test_ml_strategy_stays_out_before_enough_history() -> None:
    position = LogisticML(min_train=250).target_position(_daily_walk(400))
    assert position.iloc[:250].eq(0).all()


# --- Ciblage de volatilité et témoin constant ----------------------------------------------------------


def test_vol_target_weight_scales_down_in_turbulence() -> None:
    calm = _daily_walk(200, vol=0.01)["close"]
    wild = _daily_walk(200, vol=0.08)["close"]
    assert vol_target_weight(calm, 0.6, 20).iloc[-1] == 1.0  # vol ~19 % < cible : plafonné à 100 %
    assert vol_target_weight(wild, 0.6, 20).iloc[-1] < 0.5  # vol ~150 % : poids réduit
    w = vol_target_weight(wild, 0.6, 20).dropna()
    assert ((w * 10).round(9) % 1 == 0).all()  # arrondi au dixième


def test_rebalance_band_keeps_constant_exposure() -> None:
    df = make_ohlcv([100, 100, 150, 225, 300, 300])
    res = run_backtest(df, FixedWeight(weight=0.5), NO_COSTS, rebalance_band=0.05)
    without = run_backtest(df, FixedWeight(weight=0.5), NO_COSTS)
    assert len(res.fills) > len(without.fills) == 1  # sans bande : un seul achat, l'exposition dérive
    units = res.fills.assign(s=lambda f: f["qty"].where(f["side"] == "buy", -f["qty"]))["s"].sum()
    final_weight = units * 300 / res.equity.iloc[-1]
    assert final_weight == pytest.approx(0.5, abs=0.06)


# --- DCA --------------------------------------------------------------------------------------------


def test_dca_invests_capital_in_equal_weekly_installments() -> None:
    df = _daily_walk(400)
    res = simulate_dca(df, COSTS)
    buys = res.fills
    assert len(buys) == 52
    assert (buys["time"].diff().dropna() == pd.Timedelta(days=7)).all()
    spent = (buys["qty"] * buys["price"] + buys["fee"]).sum()
    assert spent == pytest.approx(10_000, rel=1e-6)
    assert res.position.iloc[-1] > 0.99  # tout est investi à la fin


def test_dca_without_costs_matches_hand_calculation() -> None:
    df = make_ohlcv([100.0] * 7 + [200.0] * 7 + [200.0])
    df.index = pd.date_range("2020-01-01", periods=len(df), freq="1D", tz="UTC")
    res = simulate_dca(df, NO_COSTS, installments=2)
    # 5 000 à 100 (50 unités) puis 5 000 à 200 (25 unités) → 75 unités à 200 = 15 000
    assert res.equity.iloc[-1] == pytest.approx(15_000)


# --- Critères pré-enregistrés -------------------------------------------------------------------------


def _m(sharpe: float, dd: float) -> Metrics:
    return Metrics(total_return=0.0, cagr=0.0, max_drawdown=dd, sharpe=sharpe, sortino=0.0,
                   n_trades=1, win_rate=0.5, exposure=1.0, fees_paid=0.0)


def test_criteria_require_every_condition() -> None:
    bh, const = _m(0.6, -0.77), _m(0.55, -0.45)
    assert evaluate(_m(0.8, -0.40), bh, const, 0.97).passed
    assert not evaluate(_m(0.8, -0.40), bh, const, 0.90).passed  # hasard < 95 %
    assert not evaluate(_m(0.8, -0.70), bh, const, 0.97).passed  # drawdown pas assez réduit
    assert not evaluate(_m(0.58, -0.40), bh, const, 0.97).passed  # Sharpe < buy & hold
    assert not evaluate(_m(0.62, -0.40), bh, _m(0.7, -0.4), None).passed  # Sharpe < exposition constante
    assert evaluate(_m(0.8, -0.40), bh, const, None).passed  # hasard non applicable (poids fractionnaire)


# --- Filtre de tendance adaptatif (version temps réel de H1) ------------------------------------------


def test_adaptive_trend_has_no_lookahead() -> None:
    from bot.strategies.research import AdaptiveTrend

    df = _daily_walk(1000, seed=8)
    assert find_lookahead(AdaptiveTrend(), df, n_cuts=8) == []


def test_adaptive_trend_does_not_depend_on_loaded_window() -> None:
    """En temps réel, seules les 800 dernières bougies sont chargées : la décision doit rester la même."""
    from bot.strategies.research import AdaptiveTrend

    df = _daily_walk(1500, seed=9)
    full = AdaptiveTrend().target_position(df)
    tail = AdaptiveTrend().target_position(df.tail(800))
    pd.testing.assert_series_equal(full.tail(100), tail.tail(100))


def test_adaptive_trend_reselects_on_calendar_dates_only() -> None:
    from bot.strategies.research import AdaptiveTrend

    df = _daily_walk(1200, seed=10)
    chosen = AdaptiveTrend().chosen_windows(df).dropna()
    changes = chosen[chosen.diff().fillna(0) != 0].index
    days = changes.as_unit("ns").asi8 // 86_400_000_000_000
    assert (days % 91 == 0).all()  # le choix ne change qu'aux dates de re-choix
    assert set(chosen.unique()) <= {50, 100, 150, 200}


def test_identity_survives_json_roundtrip_with_tuple_params(tmp_path) -> None:
    """Bug évité : (50, 100, 150, 200) devient [50, 100, 150, 200] une fois sauvegardé."""
    import json

    from bot.realtime.trader import make_identity
    from bot.strategies.research import AdaptiveTrend

    identity = make_identity("paper", "okx", "BTC/USDC", "1d", AdaptiveTrend(), None)
    assert json.loads(json.dumps(identity)) == identity
