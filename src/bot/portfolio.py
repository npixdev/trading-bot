"""Portefeuille spot (cash + crypto) partagé par le backtest et le paper trading."""

from __future__ import annotations

import pandas as pd


class Portfolio:
    """Cash + unités détenues, avec journal des ordres et des allers-retours.

    Long-only, sans levier : on n'achète jamais plus que le cash, on ne vend jamais plus que détenu.
    """

    def __init__(self, cash: float, fee: float, slippage: float, units: float = 0.0) -> None:
        self.cash, self.units = cash, units
        self.fee, self.slippage = fee, slippage
        self.fees_paid = 0.0
        self.fills: list[dict[str, object]] = []
        self.trades: list[dict[str, object]] = []
        self.entry: tuple[pd.Timestamp, float] | None = None

    def value(self, price: float) -> float:
        return self.cash + self.units * price

    def weight(self, price: float) -> float:
        equity = self.value(price)
        return self.units * price / equity if equity > 0 else 0.0

    def rebalance(
        self,
        time: pd.Timestamp,
        price: float,
        weight: float,
        bid: float | None = None,
        ask: float | None = None,
    ) -> dict[str, object] | None:
        """Ajuste la position vers `weight`, évaluée au prix `price`. Renvoie l'ordre exécuté.

        Achat au prix `ask` (ou `price`) × (1 + slippage), vente au `bid` (ou `price`) × (1 − slippage).
        """
        equity = self.value(price)
        delta = weight * equity - self.units * price
        if delta > 0:
            fill = (ask if ask is not None else price) * (1 + self.slippage)
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
            fill = (bid if bid is not None else price) * (1 - self.slippage)
            proceeds = qty * fill
            cost = proceeds * self.fee
            self.cash += proceeds - cost
            self.units -= qty
            side = "sell"
        else:
            return None
        self.fees_paid += cost
        order: dict[str, object] = dict(time=time, side=side, qty=qty, price=fill, fee=cost)
        self.fills.append(order)
        if weight == 0 and self.entry is not None:
            self.trades.append(make_trade(self.entry, time, self.cash, is_open=False))
            self.entry = None
        return order


def make_trade(
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
