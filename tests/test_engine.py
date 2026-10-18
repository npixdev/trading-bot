from __future__ import annotations

import pandas as pd
import pytest

from bot.backtest.engine import run_backtest, simulate
from bot.config import BacktestConfig
from bot.strategies import BuyAndHold
from tests.helpers import FixedTarget, make_ohlcv

NO_COSTS = BacktestConfig(fee=0.0, slippage=0.0, initial_cash=1000.0)
COSTS = BacktestConfig(fee=0.001, slippage=0.002, initial_cash=1000.0)


def test_flat_strategy_keeps_cash() -> None:
    df = make_ohlcv([100, 110, 90, 120])
    res = run_backtest(df, FixedTarget([0, 0, 0, 0]), COSTS)
    assert (res.equity == 1000).all()
    assert res.fills.empty and res.trades.empty


def test_buy_and_hold_without_costs_tracks_price_from_next_open() -> None:
    df = make_ohlcv([100, 110, 90, 120])
    res = run_backtest(df, BuyAndHold(), NO_COSTS)
    # Décision à la clôture de la bougie 0 → achat à l'ouverture de la bougie 1 (110).
    assert res.equity.iloc[0] == 1000
    assert res.equity.iloc[-1] == pytest.approx(1000 * 120 / 110)


def test_decision_at_t_executes_at_open_of_t_plus_1() -> None:
    """Garde-fou anti look-ahead : le signal de la bougie t n'agit qu'à partir de t+1."""
    df = make_ohlcv([100, 101, 102, 103, 104, 105, 106, 107])
    target = [0, 0, 0, 1, 0, 0, 0, 0]  # décision « acheter » à la clôture de la bougie 3
    res = run_backtest(df, FixedTarget(target), COSTS)

    pd.testing.assert_series_equal(
        res.position, pd.Series(target, index=df.index, dtype=float).shift(1).fillna(0), check_names=False
    )
    buy, sell = res.fills.iloc[0], res.fills.iloc[1]
    assert buy["time"] == df.index[4] and buy["price"] == pytest.approx(104 * 1.002)
    assert sell["time"] == df.index[5] and sell["price"] == pytest.approx(105 * 0.998)


def test_future_data_does_not_change_past_equity() -> None:
    df = make_ohlcv([100, 105, 95, 110, 120, 80, 130, 90])
    altered = df.copy()
    altered.iloc[5:] *= 3  # on change radicalement le « futur » à partir de la bougie 5
    target = [1, 0, 1, 1, 0, 1, 0, 1]
    a = run_backtest(df, FixedTarget(target), COSTS).equity
    b = run_backtest(altered, FixedTarget(target), COSTS).equity
    pd.testing.assert_series_equal(a.iloc[:5], b.iloc[:5])


def test_same_bar_cheat_is_not_rewarded() -> None:
    """Une stratégie qui « connaît » la clôture de sa propre bougie n'en profite pas grâce au t+1."""
    opens = [100, 100, 100, 100, 100, 100]
    closes = [110, 100, 110, 100, 110, 100]  # hausse sur les bougies paires
    df = make_ohlcv(opens, closes)
    cheat = (df["close"] > df["open"]).astype(float)  # « achète les bougies qui montent »
    res = simulate(df, cheat, NO_COSTS)
    # Exécutée en t+1, la position tombe sur les bougies qui ne montent pas : pas de gain.
    assert res.equity.iloc[-1] == pytest.approx(1000)


def test_round_trip_costs_are_exact() -> None:
    df = make_ohlcv([100, 100, 100, 100])
    res = run_backtest(df, FixedTarget([1, 0, 0, 0]), COSTS)
    # Achat : notional = 1000 / 1.001, prix 100.2 ; vente au prix 99.8, frais 0,1 %.
    notional = 1000 / 1.001
    qty = notional / 100.2
    final = qty * 99.8 * (1 - 0.001)
    assert res.equity.iloc[-1] == pytest.approx(final)
    assert res.fees_paid == pytest.approx(notional * 0.001 + qty * 99.8 * 0.001)
    assert final / 1000 - 1 == pytest.approx(-0.006, abs=2e-4)  # ≈ 2×0,1 % + 2×0,2 %
    assert res.trades["return"].iloc[0] == pytest.approx(final / 1000 - 1)


def test_never_spends_more_than_cash() -> None:
    df = make_ohlcv([100, 50, 200, 25, 400, 10, 10])
    res = run_backtest(df, FixedTarget([1, 0, 1, 0, 1, 0, 0]), COSTS)
    buys = res.fills[res.fills["side"] == "buy"]
    sells = res.fills[res.fills["side"] == "sell"]
    assert len(buys) == len(sells) == 3
    assert (sells["qty"].to_numpy() == pytest.approx(buys["qty"].to_numpy()))
    assert (res.equity > 0).all()


def test_target_is_clipped_to_long_only_no_leverage() -> None:
    df = make_ohlcv([100, 100, 120, 120])
    res = run_backtest(df, FixedTarget([3.0, -1.0, 0, 0]), NO_COSTS)
    assert res.position.max() == 1.0 and res.position.min() == 0.0
    assert res.equity.iloc[1] == pytest.approx(1200)  # x1 maximum, pas x3


def test_fractional_position() -> None:
    df = make_ohlcv([100, 100, 200, 200])
    res = run_backtest(df, FixedTarget([0.5, 0.5, 0.5, 0.5]), NO_COSTS)
    assert res.equity.iloc[-1] == pytest.approx(500 + 500 * 2)


def test_no_rebalancing_while_target_unchanged() -> None:
    df = make_ohlcv([100, 120, 90, 150, 80])
    res = run_backtest(df, FixedTarget([0.5] * 5), COSTS)
    assert len(res.fills) == 1


def test_start_executes_decision_from_previous_bar() -> None:
    df = make_ohlcv([100, 100, 100, 110, 120])
    res = run_backtest(df, FixedTarget([0, 0, 1, 1, 1]), NO_COSTS, start=df.index[3])
    # Décision prise à la clôture de la bougie 2 (hors période) → achat à l'ouverture de la 3.
    assert res.position.iloc[0] == 1.0
    assert res.equity.iloc[-1] == pytest.approx(1000 * 120 / 110)


def test_trades_and_open_trade() -> None:
    df = make_ohlcv([100, 100, 110, 110, 100, 100, 90, 130])
    res = run_backtest(df, FixedTarget([1, 1, 0, 1, 1, 0, 1, 1]), NO_COSTS)
    t = res.trades
    # Positions appliquées : [0, 1, 1, 0, 1, 1, 0, 1]
    assert len(t) == 3
    assert t["open"].tolist() == [False, False, True]
    assert t["return"].iloc[0] == pytest.approx(0.10)  # achat 100 (bougie 1), vente 110 (bougie 3)
    assert t["return"].iloc[1] == pytest.approx(-0.10)  # achat 100 (bougie 4), vente 90 (bougie 6)
    assert res.metrics.win_rate == pytest.approx(0.5)
