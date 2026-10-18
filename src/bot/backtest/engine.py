"""Moteur de backtest long-only, bougie par bougie.

Modèle d'exécution :
- la position cible décidée à la clôture de la bougie t est exécutée à l'**ouverture de t+1** ;
- achat au prix open × (1 + slippage), vente au prix open × (1 − slippage) ;
- frais = fraction du montant échangé, payés en cash ;
- on ne rééquilibre que lorsque la cible change (pas de micro-ordres dus à la dérive des prix) ;
- jamais de levier : on n'achète pas plus que le cash disponible, on ne vend pas plus que détenu.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.backtest.metrics import Metrics, compute_metrics
from bot.config import BacktestConfig
from bot.strategies.base import Strategy


@dataclass
class BacktestResult:
    equity: pd.Series  # valeur du portefeuille à la clôture de chaque bougie
    position: pd.Series  # poids cible effectivement appliqué pendant chaque bougie
    fills: pd.DataFrame  # un ordre exécuté par ligne
    trades: pd.DataFrame  # un aller-retour (entrée → sortie complète) par ligne
    initial_cash: float
    fees_paid: float
    metrics: Metrics = field(init=False)

    def __post_init__(self) -> None:
        self.metrics = compute_metrics(
            self.equity, self.initial_cash, self.trades, self.position, self.fees_paid
        )


def simulate(
    df: pd.DataFrame,
    target: pd.Series,
    config: BacktestConfig,
    start: pd.Timestamp | str | None = None,
    end: pd.Timestamp | str | None = None,
) -> BacktestResult:
    """Simule `target` (décisions à la clôture) sur `df`, en ne tradant que dans [start, end].

    Le décalage d'une bougie est fait ici, sur l'historique complet, *avant* de découper :
    la première bougie de la période peut donc exécuter une décision prise juste avant.
    """
    weights = target.reindex(df.index).astype(float).fillna(0.0).clip(0.0, 1.0).shift(1).fillna(0.0)
    data = df.loc[start:end]
    w = weights.loc[start:end].to_numpy()
    opens = data["open"].to_numpy()
    closes = data["close"].to_numpy()
    times = data.index
    fee, slip = config.fee, config.slippage

    cash, units, current_w = config.initial_cash, 0.0, 0.0
    fees_paid = 0.0
    equity = np.empty(len(data))
    fills: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    entry: tuple[pd.Timestamp, float] | None = None

    for i in range(len(data)):
        wi = w[i]
        if wi != current_w:
            o = opens[i]
            equity_open = cash + units * o
            delta = wi * equity_open - units * o
            if delta > 0:
                price = o * (1 + slip)
                notional = min(delta, cash / (1 + fee))
                qty = notional / price
                cost = notional * fee
                cash -= notional + cost
                units += qty
                fills.append(dict(time=times[i], side="buy", qty=qty, price=price, fee=cost))
            elif delta < 0:
                qty = units if wi == 0 else min(-delta / o, units)
                price = o * (1 - slip)
                proceeds = qty * price
                cost = proceeds * fee
                cash += proceeds - cost
                units -= qty
                fills.append(dict(time=times[i], side="sell", qty=qty, price=price, fee=cost))
            else:
                cost = 0.0
            fees_paid += cost

            if current_w == 0 and wi > 0:
                entry = (times[i], equity_open)
            elif wi == 0 and entry is not None:
                trades.append(_trade(entry, times[i], cash, is_open=False))
                entry = None
            current_w = wi
        equity[i] = cash + units * closes[i]

    if entry is not None:
        trades.append(_trade(entry, None, float(equity[-1]), is_open=True))

    return BacktestResult(
        equity=pd.Series(equity, index=times, name="equity"),
        position=pd.Series(w, index=times, name="position"),
        fills=pd.DataFrame(fills, columns=["time", "side", "qty", "price", "fee"]),
        trades=pd.DataFrame(
            trades, columns=["entry_time", "exit_time", "entry_equity", "exit_equity", "return", "open"]
        ),
        initial_cash=config.initial_cash,
        fees_paid=fees_paid,
    )


def _trade(
    entry: tuple[pd.Timestamp, float], exit_time: pd.Timestamp | None, exit_equity: float, is_open: bool
) -> dict[str, object]:
    entry_time, entry_equity = entry
    return {
        "entry_time": entry_time,
        "exit_time": exit_time,
        "entry_equity": entry_equity,
        "exit_equity": exit_equity,
        "return": exit_equity / entry_equity - 1,
        "open": is_open,
    }


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    config: BacktestConfig,
    start: pd.Timestamp | str | None = None,
    end: pd.Timestamp | str | None = None,
) -> BacktestResult:
    """Calcule le signal sur tout l'historique jusqu'à `end` (indicateurs « chauds »), trade dans [start, end]."""
    history = df.loc[:end]
    return simulate(history, strategy.target_position(history), config, start=start, end=end)
