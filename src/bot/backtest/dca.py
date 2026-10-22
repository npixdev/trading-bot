"""DCA (investissement programmé) : le capital est investi en versements égaux et réguliers, sans prédiction.

Version comparable aux stratégies : même capital de départ, investi en `installments` achats
hebdomadaires sur la première période, puis conservé. Les achats ont lieu à l'ouverture, avec
frais et slippage, comme dans le moteur.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from bot.backtest.engine import BacktestResult
from bot.config import BacktestConfig
from bot.portfolio import make_trade


def simulate_dca(
    df: pd.DataFrame,
    config: BacktestConfig,
    start: pd.Timestamp | str | None = None,
    end: pd.Timestamp | str | None = None,
    installments: int = 52,
    every: pd.Timedelta = pd.Timedelta(days=7),
) -> BacktestResult:
    data = df.loc[start:end]
    amount = config.initial_cash / installments
    cash, units, fees = config.initial_cash, 0.0, 0.0
    first = data.index[0]
    done = 0
    equity = np.empty(len(data))
    position = np.empty(len(data))
    fills: list[dict[str, object]] = []
    for i, (t, row) in enumerate(data.iterrows()):
        if done < installments and t >= first + done * every:
            price = row["open"] * (1 + config.slippage)
            notional = min(amount, cash / (1 + config.fee))
            cost = notional * config.fee
            cash -= notional + cost
            units += notional / price
            fees += cost
            done += 1
            fills.append(dict(time=t, side="buy", qty=notional / price, price=price, fee=cost))
        value = cash + units * row["close"]
        equity[i] = value
        position[i] = units * row["close"] / value if value > 0 else 0.0
    trades = pd.DataFrame(
        [make_trade((first, config.initial_cash), None, float(equity[-1]), is_open=True)],
        columns=["entry_time", "exit_time", "entry_equity", "exit_equity", "return", "open"],
    )
    return BacktestResult(
        equity=pd.Series(equity, index=data.index, name="equity"),
        position=pd.Series(position, index=data.index, name="position"),
        fills=pd.DataFrame(fills, columns=["time", "side", "qty", "price", "fee"]),
        trades=trades,
        initial_cash=config.initial_cash,
        fees_paid=fees,
    )
