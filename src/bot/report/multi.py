"""Recherche 2 (H5) : la stratégie validée appliquée indépendamment à plusieurs cryptos (docs/RECHERCHE.md).

Portefeuille = somme de N « parts » indépendantes (capital / N chacune), sans rééquilibrage entre elles.
Règles pré-enregistrées :
- une part ne trade qu'à partir de sa date d'éligibilité (assez d'historique pour la stratégie) ;
  avant, elle reste en cash — pour la stratégie comme pour chaque référence ;
- si l'historique s'arrête (retrait de la cote), la part est figée en cash à la dernière clôture.
"""

from __future__ import annotations

import logging
import zlib
from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
import pandas as pd

from bot.backtest.engine import BacktestResult, run_backtest
from bot.backtest.validation import WalkForwardResult
from bot.config import BacktestConfig
from bot.report.compare import StrategyOutcome, TimeframeSection
from bot.report.research import Criteria, evaluate
from bot.strategies.base import BuyAndHold, RandomStrategy, Strategy
from bot.strategies.research import AdaptiveTrend, FixedWeight

log = logging.getLogger(__name__)

UNIVERSE = ["BTC", "ETH", "XRP", "BCH", "EOS", "XLM", "LTC", "ADA", "IOTA", "TRX"]  # figé dans le protocole
OOS_START = pd.Timestamp("2018-08-17", tz="UTC")


@dataclass
class Sleeve:
    symbol: str
    data: pd.DataFrame
    eligible: pd.Timestamp | None  # première date où la stratégie peut décider (None : jamais)


def eligibility(df: pd.DataFrame, strategy: AdaptiveTrend) -> pd.Timestamp | None:
    chosen = strategy.chosen_windows(df).dropna()
    return None if chosen.empty else max(chosen.index[0], OOS_START)


def _sleeve_equity(result: BacktestResult | None, index: pd.DatetimeIndex, cash: float) -> pd.Series:
    """Equity d'une part sur l'index commun : cash avant le début, figée après la fin des données."""
    if result is None:
        return pd.Series(cash, index=index)
    return result.equity.reindex(index).ffill().fillna(cash)


def _sleeve_value(result: BacktestResult | None, index: pd.DatetimeIndex, cash: float) -> tuple[pd.Series, pd.Series]:
    equity = _sleeve_equity(result, index, cash)
    if result is None:
        return equity, pd.Series(0.0, index=index)
    invested = (result.position * result.equity).reindex(index).fillna(0.0)
    end = result.equity.index[-1]
    invested[invested.index > end] = 0.0  # retirée de la cote : figée en cash
    return equity, invested


def combine(results: dict[str, BacktestResult | None], index: pd.DatetimeIndex, cash: float) -> BacktestResult:
    """Additionne les parts en un portefeuille unique (pour les métriques et le rapport)."""
    equity = pd.Series(0.0, index=index)
    invested = pd.Series(0.0, index=index)
    fills, trades, fees = [], [], 0.0
    for res in results.values():
        e, inv = _sleeve_value(res, index, cash)
        equity += e
        invested += inv
        if res is not None:
            fills.append(res.fills)
            trades.append(res.trades)
            fees += res.fees_paid
    position = (invested / equity).clip(0, 1)
    cols = ["entry_time", "exit_time", "entry_equity", "exit_equity", "return", "open"]
    return BacktestResult(
        equity=equity.rename("equity"),
        position=position.rename("position"),
        fills=pd.concat(fills, ignore_index=True) if fills else pd.DataFrame(columns=["time", "side", "qty", "price", "fee"]),
        trades=pd.concat(trades, ignore_index=True) if trades else pd.DataFrame(columns=cols),
        initial_cash=cash * len(results),
        fees_paid=fees,
    )


def run_sleeves(
    sleeves: list[Sleeve],
    factory: Callable[[Sleeve], Strategy | None],
    config: BacktestConfig,
    index: pd.DatetimeIndex,
    rebalance_band: float | None = None,
) -> BacktestResult:
    cash = config.initial_cash / len(sleeves)
    sleeve_cfg = BacktestConfig(fee=config.fee, slippage=config.slippage, initial_cash=cash)
    results: dict[str, BacktestResult | None] = {}
    for s in sleeves:
        strategy = factory(s)
        if s.eligible is None or strategy is None:
            results[s.symbol] = None
            continue
        results[s.symbol] = run_backtest(s.data, strategy, sleeve_cfg, start=s.eligible, rebalance_band=rebalance_band)
    return combine(results, index, cash)


@dataclass
class MultiResult:
    section: TimeframeSection
    criteria: Criteria
    random_pct: float
    trades_per_week: float
    eligible: dict[str, str]
    average_weight: float


def run_multi(datasets: dict[str, pd.DataFrame], config: BacktestConfig, n_seeds: int = 100) -> MultiResult:
    strategy = AdaptiveTrend()
    sleeves = [Sleeve(sym, df, eligibility(df, strategy)) for sym, df in datasets.items()]
    end = max(df.index[-1] for df in datasets.values())
    index = pd.date_range(OOS_START, end, freq="1D", tz="UTC")

    strat = run_sleeves(sleeves, lambda s: AdaptiveTrend(), config, index)
    buy_hold = run_sleeves(sleeves, lambda s: BuyAndHold(), config, index)

    # Exposition moyenne de chaque part pendant sa période d'éligibilité (pour les témoins).
    per_sleeve: dict[str, tuple[float, float]] = {}
    for s in sleeves:
        if s.eligible is None:
            continue
        sub = s.data.loc[s.eligible:]
        pos = AdaptiveTrend().target_position(s.data).loc[s.eligible:]
        entries = int(((pos > 0) & (pos.shift(1, fill_value=0) == 0)).sum())
        exposure = float((pos > 0).mean())
        per_sleeve[s.symbol] = (exposure, max(1.0, exposure * len(sub) / entries) if entries else 1.0)

    average_weight = float(strat.position.mean())
    constant = run_sleeves(
        sleeves,
        lambda s: FixedWeight(weight=round(per_sleeve[s.symbol][0], 2)) if s.symbol in per_sleeve else None,
        config, index, rebalance_band=0.05,
    )

    sharpes = []
    for seed in range(n_seeds):
        def rand(s: Sleeve, seed: int = seed) -> Strategy | None:
            if s.symbol not in per_sleeve:
                return None
            exposure, hold = per_sleeve[s.symbol]
            if not 0 < exposure < 1:
                return FixedWeight(weight=exposure)
            # zlib.crc32 et non hash() : hash() d'un texte change à chaque lancement de Python → non reproductible.
            return RandomStrategy(avg_hold_bars=hold, exposure=exposure, seed=seed * 1009 + zlib.crc32(s.symbol.encode()) % 1000)
        sharpes.append(run_sleeves(sleeves, rand, config, index).metrics.sharpe)
    random_pct = float((np.nan_to_num(np.array(sharpes), nan=-np.inf) < strat.metrics.sharpe).mean())

    criteria = evaluate(strat.metrics, buy_hold.metrics, constant.metrics, random_pct)
    weeks = (index[-1] - index[0]).days / 7
    wf = WalkForwardResult(
        windows=pd.DataFrame(columns=["test_start", "test_end", "params", "train_sharpe", "test_sharpe", "test_return"]),
        equity=strat.equity, tests=[strat], initial_cash=config.initial_cash,
    )
    outcome = StrategyOutcome("trend_adaptive × 10", wf, None, verdict_text=criteria.describe())
    btc = next(s for s in sleeves if s.symbol == "BTC")
    btc_only = run_backtest(btc.data, AdaptiveTrend(), config, start=OOS_START)
    section = TimeframeSection(
        timeframe="1d", buy_hold=buy_hold, outcomes=[outcome],
        extras={f"exposition constante (~{average_weight:.0%})": constant, "H1 BTC seul": btc_only},
        title="H5 — Filtre de tendance sur 10 cryptos",
    )
    return MultiResult(
        section=section,
        criteria=criteria,
        random_pct=random_pct,
        trades_per_week=len(strat.trades) / weeks,
        eligible={s.symbol: ("jamais" if s.eligible is None else f"{s.eligible:%Y-%m-%d}") for s in sleeves},
        average_weight=average_weight,
    )


def per_asset_table(datasets: dict[str, pd.DataFrame], config: BacktestConfig) -> pd.DataFrame:
    """Détail par crypto (informatif) : stratégie vs buy & hold sur la période éligible de chacune."""
    rows = []
    for sym, df in datasets.items():
        start = eligibility(df, AdaptiveTrend())
        if start is None:
            rows.append({"crypto": sym, "début": "—"})
            continue
        s = run_backtest(df, AdaptiveTrend(), config, start=start).metrics
        b = run_backtest(df, BuyAndHold(), config, start=start).metrics
        rows.append({"crypto": sym, "début": f"{start:%Y-%m}", "fin": f"{df.index[-1]:%Y-%m}",
                     "stratégie": s.total_return, "buy&hold": b.total_return,
                     "DD strat.": s.max_drawdown, "DD b&h": b.max_drawdown,
                     "Sharpe strat.": s.sharpe, "Sharpe b&h": b.sharpe, "trades": s.n_trades})
    return pd.DataFrame(rows)
