"""Métriques de performance calculées à partir de la courbe d'equity."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

YEAR = pd.Timedelta(days=365.25)  # la crypto cote 24h/24, 7j/7


@dataclass(frozen=True)
class Metrics:
    total_return: float
    cagr: float
    max_drawdown: float
    sharpe: float
    sortino: float
    n_trades: int
    win_rate: float  # sur les trades clôturés
    exposure: float  # part du temps investi
    fees_paid: float

    def as_dict(self) -> dict[str, float]:
        return asdict(self)


def periods_per_year(index: pd.DatetimeIndex) -> float:
    step = pd.Series(index).diff().median()
    return YEAR / step


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1).min())


def compute_metrics(
    equity: pd.Series,
    initial_cash: float,
    trades: pd.DataFrame,
    position: pd.Series,
    fees_paid: float = 0.0,
) -> Metrics:
    """Sharpe et Sortino annualisés, avec un taux sans risque à 0 (convention courante en crypto)."""
    curve = pd.concat([pd.Series([initial_cash]), equity.reset_index(drop=True)])
    returns = curve.pct_change().dropna()
    ppy = periods_per_year(equity.index)

    final = float(equity.iloc[-1])
    years = (equity.index[-1] - equity.index[0] + YEAR / ppy) / YEAR
    try:
        cagr = (final / initial_cash) ** (1 / years) - 1 if final > 0 else -1.0
    except OverflowError:  # période de quelques bougies : l'annualisation n'a pas de sens
        cagr = math.inf

    std = returns.std()
    sharpe = float(returns.mean() / std * math.sqrt(ppy)) if std > 0 else math.nan
    downside = math.sqrt(float(np.mean(np.minimum(returns, 0.0) ** 2)))
    sortino = float(returns.mean() / downside * math.sqrt(ppy)) if downside > 0 else math.nan

    closed = trades[~trades["open"].astype(bool)] if len(trades) else trades
    win_rate = float((closed["return"] > 0).mean()) if len(closed) else math.nan

    return Metrics(
        total_return=final / initial_cash - 1,
        cagr=cagr,
        max_drawdown=min(max_drawdown(curve), 0.0),
        sharpe=sharpe,
        sortino=sortino,
        n_trades=len(trades),
        win_rate=win_rate,
        exposure=float((position > 0).mean()),
        fees_paid=fees_paid,
    )


def metrics_table(results: dict[str, Metrics]) -> pd.DataFrame:
    """Tableau lisible, une colonne par stratégie/période."""
    rows = {name: m.as_dict() for name, m in results.items()}
    table = pd.DataFrame(rows)
    fmt_pct = lambda x: "n/a" if pd.isna(x) else f"{x:+.1%}"  # noqa: E731
    fmt_num = lambda x: "n/a" if pd.isna(x) else f"{x:.2f}"  # noqa: E731
    formatters = {
        "total_return": fmt_pct,
        "cagr": fmt_pct,
        "max_drawdown": fmt_pct,
        "sharpe": fmt_num,
        "sortino": fmt_num,
        "n_trades": lambda x: f"{int(x)}",
        "win_rate": lambda x: "n/a" if pd.isna(x) else f"{x:.0%}",
        "exposure": lambda x: f"{x:.0%}",
        "fees_paid": lambda x: f"{x:,.0f}",
    }
    out = table.astype(object)
    for row, f in formatters.items():
        out.loc[row] = [f(v) for v in table.loc[row]]
    return out
