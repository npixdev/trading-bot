"""Brokers : transforment « je veux X % investi » en ordres, simulés ou réels.

Interface commune utilisée par le Trader :
  sync()                 relit les soldes (no-op en simulation)
  equity(price)          capital géré (cash + crypto × prix, plafonné en réel par max_capital)
  units / weight(price)  crypto détenue / part investie
  rebalance(...)         passe le(s) ordre(s) et renvoie un ExecutionResult
  state() / restore()    persistance
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count
from typing import Any, Protocol

import ccxt
import pandas as pd

from bot.config import BacktestConfig
from bot.data.fetcher import call_with_retry
from bot.portfolio import Portfolio
from bot.realtime.feed import Quote

log = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    orders: list[dict[str, Any]] = field(default_factory=list)  # ordres exécutés (même partiellement)
    done: bool = True  # la position visée est atteinte (ou rien de plus n'est faisable)
    events: list[tuple[str, str]] = field(default_factory=list)  # (type, détail) à journaliser


class Broker(Protocol):
    units: float
    fees_paid: float

    def sync(self) -> None: ...
    def equity(self, price: float) -> float: ...
    def weight(self, price: float) -> float: ...
    def rebalance(self, now: pd.Timestamp, quote: Quote, weight: float, reason: str) -> ExecutionResult: ...
    def state(self) -> dict[str, Any]: ...
    def restore(self, state: dict[str, Any]) -> None: ...


# --- Simulation -------------------------------------------------------------------------------


class PaperBroker:
    """Portefeuille fictif : exécution immédiate et complète au bid/ask + slippage + frais."""

    def __init__(self, costs: BacktestConfig) -> None:
        self.book = Portfolio(costs.initial_cash, costs.fee, costs.slippage)

    @property
    def units(self) -> float:
        return self.book.units

    @property
    def cash(self) -> float:
        return self.book.cash

    @property
    def fees_paid(self) -> float:
        return self.book.fees_paid

    def sync(self) -> None:
        pass

    def equity(self, price: float) -> float:
        return self.book.value(price)

    def weight(self, price: float) -> float:
        return self.book.weight(price)

    def rebalance(self, now: pd.Timestamp, quote: Quote, weight: float, reason: str) -> ExecutionResult:
        order = self.book.rebalance(now, quote.mid, weight, bid=quote.bid, ask=quote.ask)
        return ExecutionResult(orders=[order] if order else [])

    def state(self) -> dict[str, Any]:
        entry = None if self.book.entry is None else [self.book.entry[0].isoformat(), self.book.entry[1]]
        return {"cash": self.book.cash, "units": self.book.units, "fees_paid": self.book.fees_paid, "entry": entry}

    def restore(self, s: dict[str, Any]) -> None:
        self.book.cash, self.book.units, self.book.fees_paid = s["cash"], s["units"], s["fees_paid"]
        self.book.entry = None if s["entry"] is None else (pd.Timestamp(s["entry"][0]), s["entry"][1])


# --- Exchange réel (testnet ou live) -----------------------------------------------------------

FINAL_STATUSES = ("closed", "canceled", "expired", "rejected")


class ExchangeBroker:
    """Ordres réels via ccxt : limit IOC à prix plafonné, sur le marché spot (sans marge).

    Le solde de l'exchange fait foi : il est relu avant et après chaque ordre.
    Le bot considère tout le BTC et l'USDC du compte comme les siens → utiliser un sous-compte dédié.
    """

    def __init__(
        self,
        exchange: ccxt.Exchange,
        symbol: str,
        max_capital: float | None,
        max_slippage: float = 0.002,
        fee_buffer: float = 0.003,
        order_timeout: float = 15.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.exchange, self.symbol = exchange, symbol
        self.market = exchange.market(symbol)
        self.base, self.quote_ccy = self.market["base"], self.market["quote"]
        self.max_capital, self.max_slippage = max_capital, max_slippage
        self.fee_buffer, self.order_timeout, self.sleep = fee_buffer, order_timeout, sleep
        self.units, self.cash, self.fees_paid = 0.0, 0.0, 0.0
        self.expected_units: float | None = None
        self._ids = count()

    # --- Soldes ------------------------------------------------------------------------------

    def sync(self) -> None:
        balance = call_with_retry(self.exchange.fetch_balance, 3, self.sleep)
        self.units = float((balance.get(self.base) or {}).get("total") or 0.0)
        self.cash = float((balance.get(self.quote_ccy) or {}).get("free") or 0.0)

    def account_value(self, price: float) -> float:
        return self.cash + self.units * price

    def equity(self, price: float) -> float:
        value = self.account_value(price)
        return value if self.max_capital is None else min(value, self.max_capital)

    def weight(self, price: float) -> float:
        eq = self.equity(price)
        return min(self.units * price / eq, 1.0) if eq > 0 else 0.0

    def reconcile(self) -> str | None:
        """Compare le BTC attendu (état sauvegardé) au solde réel. Renvoie un message si écart."""
        if self.expected_units is None:
            return None
        min_amount = self.market["limits"]["amount"]["min"] or 0.0
        if abs(self.units - self.expected_units) > max(min_amount, 1e-12):
            return f"solde {self.base} réel {self.units:.8f} ≠ attendu {self.expected_units:.8f} (intervention manuelle ?)"
        return None

    # --- Ordres ------------------------------------------------------------------------------

    def rebalance(self, now: pd.Timestamp, quote: Quote, weight: float, reason: str) -> ExecutionResult:
        self.sync()
        mid = quote.mid
        delta = weight * self.equity(mid) - self.units * mid
        if delta > 0:
            side = "buy"
            limit_price = quote.ask * (1 + self.max_slippage)
            notional = min(delta, self.cash * (1 - self.fee_buffer))
            amount = self._floor_amount(notional / limit_price)
        elif delta < 0:
            side = "sell"
            limit_price = quote.bid * (1 - self.max_slippage)
            amount = self._floor_amount(self.units if weight == 0 else min(-delta / mid, self.units))
        else:
            return ExecutionResult()

        too_small = self._too_small(amount, limit_price)
        if too_small:
            # Rien de plus n'est faisable : un reliquat sous le minimum (« poussière ») reste tel quel.
            return ExecutionResult(done=True, events=[("order_too_small", f"{side} {amount:.8f} {self.base} : {too_small}")])

        result = self._place(now, side, amount, limit_price)
        self.sync()
        self.expected_units = self.units
        return result

    def _floor_amount(self, amount: float) -> float:
        if amount <= 0 or not math.isfinite(amount):
            return 0.0
        # amount_to_precision tronque (TRUNCATE) par défaut dans ccxt : jamais au-dessus du solde.
        return float(self.exchange.amount_to_precision(self.symbol, amount))

    def _too_small(self, amount: float, price: float) -> str | None:
        limits = self.market["limits"]
        min_amount = limits["amount"]["min"] or 0.0
        min_cost = (limits.get("cost") or {}).get("min") or 0.0
        if amount <= 0 or amount < min_amount:
            return f"quantité < minimum {min_amount}"
        if amount * price < min_cost:
            return f"montant {amount * price:.2f} < minimum {min_cost}"
        return None

    def _client_id(self, now: pd.Timestamp) -> str:
        return f"bot{int(now.timestamp() * 1000)}{next(self._ids)}"  # alphanumérique, ≤ 32 caractères

    def _place(self, now: pd.Timestamp, side: str, amount: float, limit_price: float) -> ExecutionResult:
        cid = self._client_id(now)
        price = float(self.exchange.price_to_precision(self.symbol, limit_price))
        params: dict[str, Any] = {"timeInForce": "IOC", "clientOrderId": cid}
        if self.exchange.id == "okx":
            params["tdMode"] = "cash"  # OKX : mode spot explicite (sans ce paramètre, Binance rejetterait l'ordre)
        events: list[tuple[str, str]] = []
        try:
            order = self.exchange.create_order(self.symbol, "limit", side, amount, price, params)
        except (ccxt.InsufficientFunds, ccxt.InvalidOrder) as exc:
            return ExecutionResult(done=False, events=[("order_rejected", f"{side} {amount:.8f} @ {price} : {exc}"[:500])])
        except ccxt.NetworkError as exc:
            # L'ordre est peut-être parti ! On demande à l'exchange plutôt que de le renvoyer.
            order = self._find_by_client_id(cid)
            if order is None:
                return ExecutionResult(
                    done=False, events=[("order_unconfirmed", f"{side} {cid} : {type(exc).__name__}, introuvable")]
                )
            events.append(("order_recovered", f"{side} {cid} retrouvé après {type(exc).__name__}"))

        order = self._wait_final(order)
        filled = float(order.get("filled") or 0.0)
        if filled <= 0:
            events.append(("order_unfilled", f"{side} {amount:.8f} @ {price} : rien d'exécuté (statut {order.get('status')})"))
            return ExecutionResult(done=False, events=events)
        avg = float(order.get("average") or order.get("price") or price)
        fee = self._fee_in_quote(order, avg)
        self.fees_paid += fee
        done = filled >= amount * (1 - 1e-9)
        if not done:
            events.append(("partial_fill", f"{side} {filled:.8f}/{amount:.8f} {self.base} exécutés à {avg:.2f}"))
        fill = dict(time=now, side=side, qty=filled, price=avg, fee=fee, id=order.get("id"), client_id=cid)
        return ExecutionResult(orders=[fill], done=done, events=events)

    def _find_by_client_id(self, cid: str) -> dict | None:
        try:
            return call_with_retry(
                lambda: self.exchange.fetch_order(None, self.symbol, {"clientOrderId": cid}), 3, self.sleep
            )
        except ccxt.OrderNotFound:
            return None

    def _wait_final(self, order: dict) -> dict:
        """Un IOC est final quasi immédiatement ; on relit l'ordre jusqu'à un statut définitif."""
        deadline = time.monotonic() + self.order_timeout
        while order.get("status") not in FINAL_STATUSES and time.monotonic() < deadline:
            self.sleep(0.5)
            order = call_with_retry(lambda: self.exchange.fetch_order(order["id"], self.symbol), 3, self.sleep)
        return order

    def _fee_in_quote(self, order: dict, avg_price: float) -> float:
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        total = 0.0
        for f in fees:
            cost = abs(float(f.get("cost") or 0.0))
            total += cost * avg_price if f.get("currency") == self.base else cost
        return total

    # --- Persistance -------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return {"units": self.units, "cash": self.cash, "fees_paid": self.fees_paid, "expected_units": self.expected_units}

    def restore(self, s: dict[str, Any]) -> None:
        self.fees_paid = s.get("fees_paid", 0.0)
        self.expected_units = s.get("expected_units", s.get("units"))
