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

    Avec `max_capital` (cas normal), le bot tient **son propre portefeuille** à l'intérieur du compte :
    il démarre avec `max_capital` en devise de cotation et 0 crypto, ne vend jamais plus que ce qu'il a
    lui-même acheté, et ignore tout ce que le compte détient par ailleurs.
    Sans `max_capital` (None), il gère le compte entier (réservé à un sous-compte dédié).
    Le solde de l'exchange fait foi : il est relu avant et après chaque ordre, et le portefeuille du bot
    est ramené au solde réel s'il le dépasse (vente manuelle, frais non comptés…).
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
        self.account_units, self.account_cash, self.fees_paid = 0.0, 0.0, 0.0
        self.bot_units, self.bot_cash = 0.0, None  # bot_cash initialisé au premier sync
        self.notes: list[str] = []  # écarts constatés avec les soldes réels, à journaliser
        self._ids = count()

    @property
    def manages_whole_account(self) -> bool:
        return self.max_capital is None

    @property
    def units(self) -> float:
        return self.account_units if self.manages_whole_account else self.bot_units

    @property
    def cash(self) -> float:
        return self.account_cash if self.manages_whole_account else (self.bot_cash or 0.0)

    # --- Soldes ------------------------------------------------------------------------------

    def resync_clock(self) -> None:
        """Remesure l'écart d'horloge PC ↔ serveur (l'horloge d'un PC dérive de quelques ms par heure)."""
        load = getattr(self.exchange, "load_time_difference", None)
        if load is not None:
            offset = load()
            log.warning("Horloge resynchronisée avec %s (écart %s ms)", self.exchange.id, offset)

    def sync(self) -> None:
        try:
            balance = self.exchange.fetch_balance()
        except ccxt.InvalidNonce:
            self.resync_clock()
            balance = call_with_retry(self.exchange.fetch_balance, 3, self.sleep)
        except ccxt.NetworkError:
            balance = call_with_retry(self.exchange.fetch_balance, 3, self.sleep)
        self.account_units = float((balance.get(self.base) or {}).get("total") or 0.0)
        self.account_cash = float((balance.get(self.quote_ccy) or {}).get("free") or 0.0)
        if self.manages_whole_account:
            return
        if self.bot_cash is None:
            self.bot_cash = min(self.max_capital, self.account_cash)  # type: ignore[type-var]
        dust = self.market["limits"]["amount"]["min"] or 0.0
        if self.bot_units > self.account_units:
            if self.bot_units - self.account_units > dust:
                self.notes.append(
                    f"le bot croyait détenir {self.bot_units:.8f} {self.base}, le compte n'en a que "
                    f"{self.account_units:.8f} (vente manuelle ?) : ramené au solde réel"
                )
            self.bot_units = self.account_units
        if self.bot_cash > self.account_cash + 0.01:
            self.notes.append(
                f"cash du bot {self.bot_cash:,.2f} > disponible {self.account_cash:,.2f} {self.quote_ccy} : ramené au solde réel"
            )
            self.bot_cash = self.account_cash

    def drain_notes(self) -> list[str]:
        notes, self.notes = self.notes, []
        return notes

    def account_value(self, price: float) -> float:
        return self.account_cash + self.account_units * price

    def equity(self, price: float) -> float:
        return self.cash + self.units * price

    def weight(self, price: float) -> float:
        eq = self.equity(price)
        return min(self.units * price / eq, 1.0) if eq > 0 else 0.0

    def _apply_fill(self, side: str, filled: float, avg: float, fee_base: float, fee_quote: float) -> None:
        """Tient à jour le portefeuille propre du bot (inutile en mode « compte entier »)."""
        if self.manages_whole_account:
            return
        assert self.bot_cash is not None
        if side == "buy":
            self.bot_units += filled - fee_base
            self.bot_cash -= filled * avg + fee_quote
        else:
            self.bot_units -= filled + fee_base
            self.bot_cash += filled * avg - fee_quote

    # --- Ordres ------------------------------------------------------------------------------

    def rebalance(self, now: pd.Timestamp, quote: Quote, weight: float, reason: str) -> ExecutionResult:
        self.sync()
        mid = quote.mid
        delta = weight * self.equity(mid) - self.units * mid
        if delta > 0:
            side = "buy"
            limit_price = quote.ask * (1 + self.max_slippage)
            available = min(self.cash, self.account_cash)
            notional = min(delta, available * (1 - self.fee_buffer))
            amount = self._floor_amount(notional / limit_price)
        elif delta < 0:
            side = "sell"
            limit_price = quote.bid * (1 - self.max_slippage)
            mine = min(self.units, self.account_units)  # jamais plus que ce que le bot a acheté lui-même
            amount = self._floor_amount(mine if weight == 0 else min(-delta / mid, mine))
        else:
            return ExecutionResult()

        too_small = self._too_small(amount, limit_price)
        if too_small:
            # Rien de plus n'est faisable : un reliquat sous le minimum (« poussière ») reste tel quel.
            return ExecutionResult(done=True, events=[("order_too_small", f"{side} {amount:.8f} {self.base} : {too_small}")])

        result = self._place(now, side, amount, limit_price)
        self.sync()
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
        except ccxt.InvalidNonce:
            # Horodatage refusé : l'exchange n'a PAS traité l'ordre. On resynchronise ; nouvel essai au prochain poll.
            self.resync_clock()
            return ExecutionResult(done=False, events=[("clock_resync", f"{side} refusé (horloge), resynchronisée")])
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
        fee_base, fee_quote = self._fees(order)
        fee = fee_base * avg + fee_quote
        self.fees_paid += fee
        self._apply_fill(side, filled, avg, fee_base, fee_quote)
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

    def _fees(self, order: dict) -> tuple[float, float]:
        """(frais payés en crypto, frais payés en devise de cotation). Binance/OKX prennent les frais
        d'un achat en crypto ; d'autres devises (ex. BNB) sont ignorées et rattrapées au sync."""
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        in_base = in_quote = 0.0
        for f in fees:
            cost = abs(float(f.get("cost") or 0.0))
            if f.get("currency") == self.base:
                in_base += cost
            elif f.get("currency") == self.quote_ccy:
                in_quote += cost
        return in_base, in_quote

    # --- Persistance -------------------------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return {
            "units": self.units,
            "cash": self.cash,
            "fees_paid": self.fees_paid,
            "bot_units": self.bot_units,
            "bot_cash": self.bot_cash,
        }

    def restore(self, s: dict[str, Any]) -> None:
        self.fees_paid = s.get("fees_paid", 0.0)
        self.bot_units = s.get("bot_units", 0.0)
        self.bot_cash = s.get("bot_cash")
