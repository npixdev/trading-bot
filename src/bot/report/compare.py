"""Walk-forward de plusieurs stratégies sur plusieurs timeframes, avec juges buy & hold et hasard."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import pandas as pd

from bot.backtest.benchmark import RandomBenchmark, random_benchmark
from bot.backtest.engine import BacktestResult, run_backtest
from bot.backtest.metrics import Metrics
from bot.backtest.validation import WalkForwardResult, walk_forward
from bot.config import BacktestConfig
from bot.data.resample import resample_ohlcv
from bot.strategies.base import BuyAndHold, Strategy

log = logging.getLogger(__name__)

# Seuil du témoin au hasard : la stratégie doit faire mieux que 90 % des singes.
RANDOM_PERCENTILE_THRESHOLD = 0.90


@dataclass
class StrategyOutcome:
    name: str
    wf: WalkForwardResult
    bench: RandomBenchmark | None
    verdict_text: str | None = None  # verdict déjà calculé (ex. critères pré-enregistrés de la recherche)

    @property
    def metrics(self) -> Metrics:
        return self.wf.metrics

    def random_percentiles(self) -> tuple[float, float] | None:
        if self.bench is None:
            return None
        return self.bench.percentile(self.metrics.total_return, self.metrics.sharpe)


@dataclass
class TimeframeSection:
    timeframe: str
    buy_hold: BacktestResult
    outcomes: list[StrategyOutcome]
    extras: dict[str, BacktestResult] = field(default_factory=dict)  # autres références (DCA, exposition constante…)
    title: str | None = None


def verdict(outcome: StrategyOutcome, buy_hold: Metrics) -> str:
    m = outcome.metrics
    pct = outcome.random_percentiles()
    if pct is None:
        return "aucun trade hors échantillon"
    if pct[1] < RANDOM_PERCENTILE_THRESHOLD:
        return "indiscernable du hasard"
    if m.total_return > buy_hold.total_return and m.sharpe > buy_hold.sharpe:
        return "bat le buy & hold (rendement et Sharpe)"
    if m.sharpe > buy_hold.sharpe and m.max_drawdown > buy_hold.max_drawdown:
        return "rendement inférieur au buy & hold, mais moins risqué (Sharpe et drawdown meilleurs)"
    return "ne bat pas le buy & hold"


def bars_for(duration: str, df: pd.DataFrame) -> int:
    step = df.index.to_series().diff().median()
    return int(pd.Timedelta(duration) / step)


def run_comparison(
    df: pd.DataFrame,
    timeframes: Sequence[str],
    strategies: Sequence[type[Strategy]],
    config: BacktestConfig,
    train: str = "365D",
    test: str = "91D",
    n_seeds: int = 100,
) -> list[TimeframeSection]:
    sections: list[TimeframeSection] = []
    for tf in timeframes:
        data = resample_ohlcv(df, tf)
        train_bars, test_bars = bars_for(train, data), bars_for(test, data)
        outcomes: list[StrategyOutcome] = []
        for cls in strategies:
            log.info("%s %s : walk-forward (%d combinaisons)", tf, cls.name, _grid_size(cls))
            wf = walk_forward(data, cls, cls.grid, config, train_bars, test_bars)  # type: ignore[attr-defined]
            bench = random_benchmark(data, wf.position, len(wf.trades), config, n_seeds)
            outcomes.append(StrategyOutcome(cls.name, wf, bench))
        start, end = outcomes[0].wf.equity.index[0], outcomes[0].wf.equity.index[-1]
        buy_hold = run_backtest(data, BuyAndHold(), config, start=start, end=end)
        sections.append(TimeframeSection(tf, buy_hold, outcomes))
    return sections


def _grid_size(cls: type[Strategy]) -> int:
    size = 1
    for values in getattr(cls, "grid", {}).values():
        size *= len(values)
    return size
