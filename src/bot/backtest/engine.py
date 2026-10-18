"""Moteur de backtest long-only, bougie par bougie.

Modèle d'exécution :
- la position cible décidée à la clôture de la bougie t est exécutée à l'**ouverture de t+1** ;
- achat au prix open × (1 + slippage), vente au prix open × (1 − slippage) ;
- frais = fraction du montant échangé, payés en cash ;
- on ne rééquilibre que lorsque la cible change (pas de micro-ordres dus à la dérive des prix) ;
- jamais de levier : on n'achète pas plus que le cash disponible, on ne vend pas plus que détenu.

Avec un RiskManager (optionnel) :
- la taille d'entrée et le stop utilisent l'ATR de la bougie **précédente** (connu à l'ouverture) ;
- le stop est vérifié pendant la bougie avec son plus bas ; sortie au stop, ou à l'ouverture en cas de gap ;
- limite journalière et kill switch sont évalués à la clôture et appliqués à l'ouverture suivante ;
- si une hausse fait dériver l'exposition au-delà du plafond (+ tolérance), le surplus est revendu à l'ouverture.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from bot.backtest.metrics import Metrics, compute_metrics
from bot.config import BacktestConfig, RiskConfig
from bot.risk.manager import RiskManager
from bot.strategies.base import Strategy
from bot.strategies.indicators import atr as compute_atr


@dataclass
class BacktestResult:
    equity: pd.Series  # valeur du portefeuille à la clôture de chaque bougie
    position: pd.Series  # poids effectivement visé pendant chaque bougie (après garde-fous)
    fills: pd.DataFrame  # un ordre exécuté par ligne
    trades: pd.DataFrame  # un aller-retour (entrée → sortie complète) par ligne
    initial_cash: float
    fees_paid: float
    events: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=["time", "kind", "detail"]))
    metrics: Metrics = field(init=False)

    def __post_init__(self) -> None:
        self.metrics = compute_metrics(
            self.equity, self.initial_cash, self.trades, self.position, self.fees_paid
        )


class _Portfolio:
    """Cash + unités détenues, avec journal des ordres et des allers-retours."""

    def __init__(self, cash: float, fee: float, slippage: float) -> None:
        self.cash, self.units = cash, 0.0
        self.fee, self.slippage = fee, slippage
        self.fees_paid = 0.0
        self.fills: list[dict[str, object]] = []
        self.trades: list[dict[str, object]] = []
        self.entry: tuple[pd.Timestamp, float] | None = None

    def value(self, price: float) -> float:
        return self.cash + self.units * price

    def rebalance(self, time: pd.Timestamp, price: float, weight: float) -> float | None:
        """Ajuste la position vers `weight` au prix `price` (avant slippage). Renvoie le prix d'exécution."""
        equity = self.value(price)
        delta = weight * equity - self.units * price
        if delta > 0:
            fill = price * (1 + self.slippage)
            notional = min(delta, self.cash / (1 + self.fee))
            qty = notional / fill
            cost = notional * self.fee
            self.cash -= notional + cost
            self.units += qty
            side = "buy"
            if self.entry is None:
                self.entry = (time, equity)
        elif delta < 0:
            qty = self.units if weight == 0 else min(-delta / price, self.units)
            fill = price * (1 - self.slippage)
            proceeds = qty * fill
            cost = proceeds * self.fee
            self.cash += proceeds - cost
            self.units -= qty
            side = "sell"
        else:
            return None
        self.fees_paid += cost
        self.fills.append(dict(time=time, side=side, qty=qty, price=fill, fee=cost))
        if weight == 0 and self.entry is not None:
            self.trades.append(_trade(self.entry, time, self.cash, is_open=False))
            self.entry = None
        return fill


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


def simulate(
    df: pd.DataFrame,
    target: pd.Series,
    config: BacktestConfig,
    start: pd.Timestamp | str | None = None,
    end: pd.Timestamp | str | None = None,
    risk: RiskConfig | None = None,
) -> BacktestResult:
    """Simule `target` (décisions à la clôture) sur `df`, en ne tradant que dans [start, end].

    Le décalage d'une bougie est fait ici, sur l'historique complet, *avant* de découper :
    la première bougie de la période peut donc exécuter une décision prise juste avant.
    """
    signals = target.reindex(df.index).astype(float).fillna(0.0).clip(0.0, 1.0).shift(1).fillna(0.0)
    data = df.loc[start:end]
    signal = signals.loc[start:end].to_numpy()
    opens, lows, closes = (data[c].to_numpy() for c in ("open", "low", "close"))
    times = data.index

    manager = RiskManager(risk) if risk is not None else None
    # ATR connu à l'ouverture de t = ATR calculé à la clôture de t-1.
    atr_prev = (
        compute_atr(df, risk.atr_period).shift(1).loc[start:end].to_numpy()
        if risk is not None
        else np.full(len(data), np.nan)
    )

    book = _Portfolio(config.initial_cash, config.fee, config.slippage)
    current_w = 0.0
    equity = np.empty(len(data))
    applied = np.empty(len(data))

    for i in range(len(data)):
        t, o = times[i], opens[i]
        wi = signal[i]
        if manager is not None:
            manager.on_bar_open(t, book.value(o))
            wi = manager.target_weight(wi, current_w, o, atr_prev[i])
        if wi != current_w:
            fill = book.rebalance(t, o, wi)
            if manager is not None and current_w == 0 and wi > 0 and fill is not None:
                manager.on_entry(fill, atr_prev[i])
            elif manager is not None and wi == 0:
                manager.on_exit()
            current_w = wi
        if manager is not None and book.units > 0:
            cap = manager.exposure_cap(t, book.units * o / book.value(o))
            if cap is not None:
                book.rebalance(t, o, cap)
                current_w = cap
        applied[i] = current_w
        if manager is not None and book.units > 0:
            stop_price = manager.check_stop(t, o, lows[i])
            if stop_price is not None:
                book.rebalance(t, stop_price, 0.0)
                current_w = 0.0
        equity[i] = book.value(closes[i])
        if manager is not None:
            manager.on_bar_close(t, equity[i])

    if book.entry is not None:
        book.trades.append(_trade(book.entry, None, float(equity[-1]), is_open=True))

    events = pd.DataFrame(
        [vars(e) for e in manager.events] if manager else [], columns=["time", "kind", "detail"]
    )
    return BacktestResult(
        equity=pd.Series(equity, index=times, name="equity"),
        position=pd.Series(applied, index=times, name="position"),
        fills=pd.DataFrame(book.fills, columns=["time", "side", "qty", "price", "fee"]),
        trades=pd.DataFrame(
            book.trades, columns=["entry_time", "exit_time", "entry_equity", "exit_equity", "return", "open"]
        ),
        initial_cash=config.initial_cash,
        fees_paid=book.fees_paid,
        events=events,
    )


def run_backtest(
    df: pd.DataFrame,
    strategy: Strategy,
    config: BacktestConfig,
    start: pd.Timestamp | str | None = None,
    end: pd.Timestamp | str | None = None,
    risk: RiskConfig | None = None,
) -> BacktestResult:
    """Calcule le signal sur tout l'historique jusqu'à `end` (indicateurs « chauds »), trade dans [start, end]."""
    history = df.loc[:end]
    return simulate(history, strategy.target_position(history), config, start=start, end=end, risk=risk)
