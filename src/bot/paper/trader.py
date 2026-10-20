"""Logique du paper trader : une méthode `step()` appelée à intervalle régulier.

À chaque appel :
1. si une nouvelle bougie a clôturé : garde-fous de clôture → signal sur les bougies clôturées →
   garde-fous d'ouverture → ordre simulé au bid/ask du moment (= ouverture de la bougie suivante) ;
2. dans tous les cas : vérification du stop-loss au bid du moment.
Tout est journalisé et l'état est sauvegardé dans la même transaction SQLite.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict
from typing import Any, Protocol

import pandas as pd

from bot.config import BacktestConfig, RiskConfig
from bot.data.fetcher import timeframe_ms
from bot.paper.feed import Quote
from bot.paper.store import Store
from bot.portfolio import Portfolio
from bot.risk.manager import RiskManager
from bot.strategies.base import Strategy
from bot.strategies.indicators import atr as compute_atr

log = logging.getLogger(__name__)


class Feed(Protocol):
    timeframe: str

    def closed_candles(self, n: int) -> pd.DataFrame: ...
    def quote(self) -> Quote: ...
    def now(self) -> pd.Timestamp: ...


class ConfigMismatch(RuntimeError):
    """Le journal existant a été créé avec une autre stratégie / un autre marché."""


class PaperTrader:
    def __init__(
        self,
        feed: Feed,
        store: Store,
        strategy: Strategy,
        costs: BacktestConfig,
        risk: RiskConfig | None,
        identity: dict[str, Any],
        warmup_bars: int = 300,
    ) -> None:
        self.feed, self.store, self.strategy = feed, store, strategy
        self.risk_config, self.warmup_bars = risk, warmup_bars
        self.tf = pd.Timedelta(milliseconds=timeframe_ms(feed.timeframe))
        self.identity = identity
        state = store.load_state()
        if state is None:
            self.book = Portfolio(costs.initial_cash, costs.fee, costs.slippage)
            self.risk = RiskManager(risk) if risk is not None else None
            self.current_weight = 0.0
            self.last_bar: pd.Timestamp | None = None
            self.started: dict[str, Any] | None = None
        else:
            if state["identity"] != identity:
                old = state["identity"]
                diffs = ", ".join(
                    f"{k} : {old.get(k)} → {identity.get(k)}" for k in identity if old.get(k) != identity.get(k)
                )
                raise ConfigMismatch(
                    f"Le journal {store.path} a été créé avec une autre configuration ({diffs}). "
                    "Utilise un autre --db, ou --reset pour repartir de zéro."
                )
            self._restore(state, costs)

    # --- Persistance -----------------------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        entry = None if self.book.entry is None else [self.book.entry[0].isoformat(), self.book.entry[1]]
        return {
            "identity": self.identity,
            "cash": self.book.cash,
            "units": self.book.units,
            "fees_paid": self.book.fees_paid,
            "entry": entry,
            "current_weight": self.current_weight,
            "last_bar": None if self.last_bar is None else self.last_bar.isoformat(),
            "risk": None if self.risk is None else self.risk.to_dict(),
            "started": self.started,
        }

    def _restore(self, s: dict[str, Any], costs: BacktestConfig) -> None:
        self.book = Portfolio(s["cash"], costs.fee, costs.slippage, units=s["units"])
        self.book.fees_paid = s["fees_paid"]
        self.book.entry = None if s["entry"] is None else (pd.Timestamp(s["entry"][0]), s["entry"][1])
        self.current_weight = s["current_weight"]
        self.last_bar = None if s["last_bar"] is None else pd.Timestamp(s["last_bar"])
        self.risk = (
            RiskManager.from_dict(self.risk_config, s["risk"])
            if self.risk_config is not None and s["risk"] is not None
            else (RiskManager(self.risk_config) if self.risk_config is not None else None)
        )
        self.started = s["started"]

    def _flush(self) -> None:
        """Écrit les événements de risque en attente + l'état, puis valide la transaction."""
        if self.risk is not None:
            for ev in self.risk.events:
                self.store.log_event(ev.time, ev.kind, ev.detail)
                log.warning("Garde-fou %s : %s", ev.kind, ev.detail)
            self.risk.events.clear()
        self.store.save_state(self._state())
        self.store.commit()

    # --- Boucle ----------------------------------------------------------------------------

    def step(self) -> None:
        now = self.feed.now()
        candles = self.feed.closed_candles(self.warmup_bars)
        quote = self.feed.quote()
        if candles.empty:
            return
        if self.started is None:
            self.started = {"time": now.isoformat(), "price": quote.mid, "equity": self.book.value(quote.mid)}
            self.store.log_event(now, "start", f"démarrage, BTC à {quote.mid:,.2f}, capital {self.book.value(quote.mid):,.2f}")
        if self.last_bar is None or candles.index[-1] > self.last_bar:
            self._on_new_bar(now, candles, quote)
        self._check_stop(now, quote)
        self._flush()

    def _on_new_bar(self, now: pd.Timestamp, candles: pd.DataFrame, quote: Quote) -> None:
        bar = candles.index[-1]
        close = float(candles["close"].iloc[-1])
        if self.last_bar is not None:
            missed = int((bar - self.last_bar) / self.tf) - 1
            if missed > 0:
                self.store.log_event(now, "resume", f"{missed} bougie(s) non traitée(s) pendant l'arrêt : pas de rattrapage")
            if self.risk is not None:
                self.risk.on_bar_close(bar, self.book.value(close))

        signal = float(min(max(self.strategy.target_position(candles).iloc[-1], 0.0), 1.0))
        if math.isnan(signal):
            signal = 0.0
        price = quote.mid
        target = signal
        note = ""
        atr_now = math.nan
        if self.risk is not None:
            # ATR à la clôture de la dernière bougie = connu à l'ouverture de la suivante.
            atr_now = float(compute_atr(candles, self.risk_config.atr_period).iloc[-1])  # type: ignore[union-attr]
            self.risk.on_bar_open(bar + self.tf, self.book.value(price))
            target = self.risk.target_weight(signal, self.current_weight, price, atr_now)
            if self.risk.killed:
                note = "kill switch actif"
            elif target == 0 and signal > 0:
                note = "bloqué par un garde-fou"

        previous = self.current_weight
        if target != self.current_weight:
            self._rebalance(now, quote, target, reason="signal")
            if self.risk is not None:
                if previous == 0 and target > 0 and self.book.fills:
                    self.risk.on_entry(float(self.book.fills[-1]["price"]), atr_now)  # type: ignore[arg-type]
                elif target == 0:
                    self.risk.on_exit()
        if self.risk is not None and self.book.units > 0:
            cap = self.risk.exposure_cap(now, self.book.weight(price))
            if cap is not None:
                self._rebalance(now, quote, cap, reason="exposure")

        equity = self.book.value(price)
        self.store.log_decision(now, bar, close, signal, previous, self.current_weight, equity, note)
        self.store.log_equity(now, equity, self.book.cash, self.book.units, price)
        self.last_bar = bar
        log.info(
            "Bougie %s clôturée à %.2f : signal %.0f, position %.0f%% → %.0f%%, equity %.2f %s",
            bar, close, signal, previous * 100, self.current_weight * 100, equity, note,
        )

    def _check_stop(self, now: pd.Timestamp, quote: Quote) -> None:
        if self.risk is None or self.book.units <= 0:
            return
        if self.risk.check_stop(now, quote.bid, quote.bid) is not None:
            self._rebalance(now, quote, 0.0, reason="stop")
            self.store.log_equity(now, self.book.value(quote.mid), self.book.cash, self.book.units, quote.mid)

    def _rebalance(self, now: pd.Timestamp, quote: Quote, weight: float, reason: str) -> None:
        order = self.book.rebalance(now, quote.mid, weight, bid=quote.bid, ask=quote.ask)
        self.current_weight = weight
        if order is not None:
            self.store.log_order(order, quote.bid, quote.ask, reason)
            log.info("Ordre simulé %s %.6f à %.2f (frais %.2f) — %s", order["side"], order["qty"], order["price"], order["fee"], reason)


def make_identity(exchange: str, symbol: str, timeframe: str, strategy: Strategy, risk: RiskConfig | None) -> dict[str, Any]:
    """Ce qui doit rester identique pour reprendre un journal existant."""
    return {
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": strategy.name,
        "params": strategy.params,
        "risk": None if risk is None else asdict(risk),
    }
