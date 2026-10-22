"""Exécution du protocole pré-enregistré (docs/RECHERCHE.md) : H1 à H4, juges et critères fixés à l'avance.

Rien ici ne doit être ajusté après avoir vu les résultats : les grilles viennent des classes de
stratégie, les critères de CRITERIA ci-dessous, conformes au document commité avant l'exécution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

from bot.backtest.benchmark import random_benchmark
from bot.backtest.dca import simulate_dca
from bot.backtest.engine import BacktestResult, run_backtest
from bot.backtest.metrics import Metrics
from bot.backtest.validation import walk_forward
from bot.config import BacktestConfig
from bot.report.compare import StrategyOutcome, TimeframeSection, bars_for
from bot.strategies.base import BuyAndHold, Strategy
from bot.strategies.research import FixedWeight, LogisticML, TrendFilter, TrendVol, VolTarget

log = logging.getLogger(__name__)

HYPOTHESES: list[tuple[str, type[Strategy]]] = [
    ("H1 — Filtre de tendance", TrendFilter),
    ("H2 — Ciblage de volatilité", VolTarget),
    ("H3 — Tendance + volatilité", TrendVol),
    ("H4 — IA : régression logistique", LogisticML),
]

# Critères pré-enregistrés (docs/RECHERCHE.md, section « Critères de succès »).
MIN_DRAWDOWN_IMPROVEMENT = 0.10  # au moins 10 points de drawdown en moins que le buy & hold
RANDOM_THRESHOLD = 0.95  # battre au moins 95 % des stratégies aléatoires (H1, H4)
CONSTANT_BAND = 0.05  # témoin à exposition constante réajusté au-delà de 5 points d'écart


@dataclass
class Criteria:
    sharpe_vs_buy_hold: bool
    sharpe_vs_constant: bool
    drawdown: bool
    random: bool | None  # None : non applicable (stratégie à poids fractionnaire)

    @property
    def passed(self) -> bool:
        return self.sharpe_vs_buy_hold and self.sharpe_vs_constant and self.drawdown and self.random is not False

    def describe(self) -> str:
        mark = {True: "✔", False: "✘", None: "—"}
        verdict = "PROMETTEUSE (à confirmer en paper trading)" if self.passed else "ÉCHEC du protocole"
        return (
            f"{verdict} · Sharpe > buy & hold {mark[self.sharpe_vs_buy_hold]} · "
            f"Sharpe > exposition constante {mark[self.sharpe_vs_constant]} · "
            f"drawdown −10 pts {mark[self.drawdown]} · hasard ≥ 95 % {mark[self.random]}"
        )


def evaluate(m: Metrics, buy_hold: Metrics, constant: Metrics, random_pct: float | None) -> Criteria:
    return Criteria(
        sharpe_vs_buy_hold=m.sharpe > buy_hold.sharpe,
        sharpe_vs_constant=m.sharpe > constant.sharpe,
        drawdown=m.max_drawdown >= buy_hold.max_drawdown + MIN_DRAWDOWN_IMPROVEMENT,
        random=None if random_pct is None else random_pct >= RANDOM_THRESHOLD,
    )


@dataclass
class HypothesisResult:
    label: str
    section: TimeframeSection
    criteria: Criteria
    average_weight: float


def run_research(
    df: pd.DataFrame,
    config: BacktestConfig,
    train: str = "365D",
    test: str = "91D",
    n_seeds: int = 100,
) -> list[HypothesisResult]:
    train_bars, test_bars = bars_for(train, df), bars_for(test, df)
    results: list[HypothesisResult] = []
    for label, cls in HYPOTHESES:
        log.info("%s : walk-forward (%s)", label, cls.grid)  # type: ignore[attr-defined]
        wf = walk_forward(df, cls, cls.grid, config, train_bars, test_bars)  # type: ignore[attr-defined]
        start, end = wf.equity.index[0], wf.equity.index[-1]
        buy_hold = run_backtest(df, BuyAndHold(), config, start=start, end=end)
        dca = simulate_dca(df, config, start=start, end=end)
        average_weight = float(wf.position.mean())
        constant: BacktestResult = run_backtest(
            df, FixedWeight(weight=round(average_weight, 2)), config, start=start, end=end, rebalance_band=CONSTANT_BAND
        )
        bench = None
        random_pct = None
        if getattr(cls, "binary", False):
            bench = random_benchmark(df, wf.position, len(wf.trades), config, n_seeds)
            if bench is not None:
                random_pct = bench.percentile(wf.metrics.total_return, wf.metrics.sharpe)[1]
        criteria = evaluate(wf.metrics, buy_hold.metrics, constant.metrics, random_pct)
        outcome = StrategyOutcome(cls.name, wf, bench, verdict_text=criteria.describe())
        section = TimeframeSection(
            timeframe="1d",
            buy_hold=buy_hold,
            outcomes=[outcome],
            extras={"DCA 52 semaines": dca, f"exposition constante {average_weight:.0%}": constant},
            title=label,
        )
        results.append(HypothesisResult(label, section, criteria, average_weight))
    return results
