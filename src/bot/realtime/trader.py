"""Trader temps réel, commun aux modes paper, testnet et live : une méthode `step()` appelée en boucle.

À chaque appel :
1. relecture des soldes (broker.sync) ;
2. si une nouvelle bougie a clôturé : garde-fous de clôture → signal sur les bougies clôturées →
   garde-fous d'ouverture → ordre au prix du moment (= ouverture de la bougie suivante) ;
3. sinon, si un ordre précédent n'a pas atteint sa cible (partiel, rejeté) : nouvel essai ;
4. dans tous les cas : vérification du stop-loss au bid du moment.
Tout est journalisé ; l'état est sauvegardé dans la même transaction SQLite.
Le Trader ne parle jamais à l'exchange pour trader : il passe par un broker (simulé ou réel).
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict
from typing import TYPE_CHECKING, Any, Protocol

import pandas as pd

from bot.config import RiskConfig
from bot.data.fetcher import timeframe_ms
from bot.realtime.feed import Quote
from bot.realtime.store import Store
from bot.risk.manager import RiskManager
from bot.strategies.base import Strategy
from bot.strategies.indicators import atr as compute_atr

if TYPE_CHECKING:
    from bot.execution.broker import Broker, ExecutionResult

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # essais pour atteindre une cible > 0 (une sortie, elle, est réessayée sans limite)


class Feed(Protocol):
    timeframe: str

    def closed_candles(self, n: int) -> pd.DataFrame: ...
    def quote(self) -> Quote: ...
    def now(self) -> pd.Timestamp: ...


class ConfigMismatch(RuntimeError):
    """Le journal existant a été créé avec une autre stratégie / un autre marché."""


class Trader:
    def __init__(
        self,
        feed: Feed,
        store: Store,
        strategy: Strategy,
        broker: Broker,
        risk: RiskConfig | None,
        identity: dict[str, Any],
        warmup_bars: int = 300,
    ) -> None:
        self.feed, self.store, self.strategy, self.broker = feed, store, strategy, broker
        self.risk_config, self.warmup_bars = risk, warmup_bars
        self.tf = pd.Timedelta(milliseconds=timeframe_ms(feed.timeframe))
        self.identity = identity
        self.risk = RiskManager(risk) if risk is not None else None
        self.current_weight = 0.0
        self.pending: float | None = None  # cible pas encore atteinte
        self.attempts = 0
        self.last_bar: pd.Timestamp | None = None
        self.started: dict[str, Any] | None = None
        self._reconciled = False
        self._warned_history = False
        self.signal_info: dict[str, Any] | None = None  # explication de la décision, pour Telegram
        state = store.load_state()
        if state is not None:
            state = _migrate_state(state)
            if state["identity"] != identity:
                old = state["identity"]
                diffs = ", ".join(
                    f"{k} : {old.get(k)} → {identity.get(k)}" for k in identity if old.get(k) != identity.get(k)
                )
                raise ConfigMismatch(
                    f"Le journal {store.path} a été créé avec une autre configuration ({diffs}). "
                    "Utilise un autre --db, ou --reset pour repartir de zéro."
                )
            self._restore(state)

    # --- Persistance -----------------------------------------------------------------------

    def _state(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "broker": self.broker.state(),
            "current_weight": self.current_weight,
            "pending": self.pending,
            "attempts": self.attempts,
            "last_bar": None if self.last_bar is None else self.last_bar.isoformat(),
            "risk": None if self.risk is None else self.risk.to_dict(),
            "started": self.started,
            "signal_info": self.signal_info,
        }

    def _restore(self, s: dict[str, Any]) -> None:
        self.broker.restore(s["broker"])
        self.current_weight, self.pending, self.attempts = s["current_weight"], s["pending"], s["attempts"]
        self.last_bar = None if s["last_bar"] is None else pd.Timestamp(s["last_bar"])
        if self.risk_config is not None and s["risk"] is not None:
            self.risk = RiskManager.from_dict(self.risk_config, s["risk"])
        self.started = s["started"]
        self.signal_info = s.get("signal_info")

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
        self.broker.sync()
        self._log_broker_notes(now)
        if len(candles) < self.warmup_bars * 0.95:
            # Vécu : OKX ne garde que ~470 jours de BTC/USDC journalier → le bot restait muet, sans décision.
            if not self._warned_history:
                self._warned_history = True
                detail = (f"seulement {len(candles)} bougies disponibles sur {self.warmup_bars} demandées : "
                          "la stratégie risque de ne jamais pouvoir décider (choisir une paire avec plus d'historique)")
                self.store.log_event(now, "history_short", detail)
                log.warning(detail)
                self.store.commit()
            if candles.empty:
                return
        if not self._reconciled:
            self._reconcile(now, quote)
        if self.started is None:
            equity = self.broker.equity(quote.mid)
            self.started = {"time": now.isoformat(), "price": quote.mid, "equity": equity}
            self.store.log_event(now, "start", f"démarrage, prix {quote.mid:,.2f}, capital géré {equity:,.2f}")
        if self.last_bar is None or candles.index[-1] > self.last_bar:
            self._on_new_bar(now, candles, quote)
        elif self.pending is not None:
            self._retry(now, quote)
        if self.signal_info is None:  # journal plus ancien que cette fonctionnalité, ou premier démarrage
            self.signal_info = self.strategy.explain(candles)
        self._check_stop(now, quote)
        self.store.set_meta("last_price", json.dumps({"time": now.isoformat(), "price": quote.mid}))
        self._flush()

    def _log_broker_notes(self, now: pd.Timestamp) -> None:
        """Écarts entre le portefeuille du bot et les soldes réels, constatés par le broker."""
        for note in getattr(self.broker, "drain_notes", list)():
            self.store.log_event(now, "reconcile", note)
            log.warning("Réconciliation : %s", note)

    def _reconcile(self, now: pd.Timestamp, quote: Quote) -> None:
        """Au démarrage : l'exchange fait foi. Aligne la position connue sur le solde réel."""
        self._reconciled = True
        actual = self.broker.weight(quote.mid)
        if self.current_weight > 0 and actual == 0:
            self.current_weight = 0.0
            if self.risk is not None:
                self.risk.on_exit()
        elif self.current_weight == 0 and actual > 0:
            self.current_weight = actual
            self.store.log_event(now, "reconcile", f"position existante de {actual:.0%} reprise sans stop-loss")

    def _on_new_bar(self, now: pd.Timestamp, candles: pd.DataFrame, quote: Quote) -> None:
        bar = candles.index[-1]
        close = float(candles["close"].iloc[-1])
        if self.last_bar is not None:
            missed = int((bar - self.last_bar) / self.tf) - 1
            if missed > 0:
                self.store.log_event(now, "resume", f"{missed} bougie(s) non traitée(s) pendant l'arrêt : pas de rattrapage")
            if self.risk is not None:
                self.risk.on_bar_close(bar, self.broker.equity(close))

        signal = float(min(max(self.strategy.target_position(candles).iloc[-1], 0.0), 1.0))
        if math.isnan(signal):
            signal = 0.0
        self.signal_info = self.strategy.explain(candles)
        price = quote.mid
        target, note, atr_now = signal, "", math.nan
        if self.risk is not None:
            # ATR à la clôture de la dernière bougie = connu à l'ouverture de la suivante.
            atr_now = float(compute_atr(candles, self.risk_config.atr_period).iloc[-1])  # type: ignore[union-attr]
            self.risk.on_bar_open(bar + self.tf, self.broker.equity(price))
            target = self.risk.target_weight(signal, self.current_weight, price, atr_now)
            if self.risk.killed:
                note = "kill switch actif"
            elif target == 0 and signal > 0:
                note = "bloqué par un garde-fou"

        previous = self.current_weight
        self.pending, self.attempts = None, 0
        if target != self.current_weight:
            result = self._execute(now, quote, target, reason="signal")
            if self.risk is not None:
                if previous == 0 and self.current_weight > 0 and result.orders:
                    self.risk.on_entry(float(result.orders[-1]["price"]), atr_now)
                elif target == 0 and self.current_weight == 0:
                    self.risk.on_exit()
        if self.risk is not None and self.broker.units > 0:
            cap = self.risk.exposure_cap(now, self.broker.weight(price))
            if cap is not None:
                self._execute(now, quote, cap, reason="exposure")

        equity = self.broker.equity(price)
        self.store.log_decision(now, bar, close, signal, previous, self.current_weight, equity, note)
        self.store.log_equity(now, equity, getattr(self.broker, "cash", math.nan), self.broker.units, price)
        self.last_bar = bar
        log.info(
            "Bougie %s clôturée à %.2f : signal %.0f, position %.0f%% → %.0f%%, equity %.2f %s",
            bar, close, signal, previous * 100, self.current_weight * 100, equity, note,
        )

    def _retry(self, now: pd.Timestamp, quote: Quote) -> None:
        target = self.pending
        assert target is not None
        if target > 0 and self.attempts >= MAX_ATTEMPTS:
            self.store.log_event(now, "order_gave_up", f"cible {target:.0%} abandonnée après {self.attempts} essais, "
                                 f"position réelle {self.broker.weight(quote.mid):.0%}")
            self.current_weight = self.broker.weight(quote.mid)
            self.pending, self.attempts = None, 0
            return
        was_flat = self.broker.units == 0
        result = self._execute(now, quote, target, reason="retry")
        if self.risk is not None and was_flat and self.broker.units > 0 and result.orders and self.risk.stop is None:
            self.risk.on_entry(float(result.orders[-1]["price"]), math.nan)

    def on_tick(self, quote: Quote) -> bool:
        """Nouveau prix poussé par le flux WebSocket : vérifie seulement le stop (aucune requête).

        Appelé des centaines de fois par minute : ne fait rien tant qu'il n'y a pas de stop à surveiller.
        Renvoie True si le stop a été déclenché.
        """
        if self.risk is None or self.risk.stop is None or self.broker.units <= 0:
            return False
        if quote.bid > self.risk.stop:
            return False
        now = self.feed.now()
        self._check_stop(now, quote)
        self._flush()
        return True

    def _check_stop(self, now: pd.Timestamp, quote: Quote) -> None:
        if self.risk is None or self.broker.units <= 0:
            return
        if self.risk.check_stop(now, quote.bid, quote.bid) is not None:
            self._execute(now, quote, 0.0, reason="stop")
            self.store.log_equity(now, self.broker.equity(quote.mid), getattr(self.broker, "cash", math.nan),
                                  self.broker.units, quote.mid)

    def _execute(self, now: pd.Timestamp, quote: Quote, target: float, reason: str) -> ExecutionResult:
        result = self.broker.rebalance(now, quote, target, reason)
        self._log_broker_notes(now)
        for order in result.orders:
            self.store.log_order(order, quote.bid, quote.ask, reason)
            log.info("Ordre %s %.8f à %.2f (frais %.4f) — %s", order["side"], order["qty"], order["price"], order["fee"], reason)
        for kind, detail in result.events:
            self.store.log_event(now, kind, detail)
            log.warning("%s : %s", kind, detail)
        if result.done:
            self.current_weight, self.pending, self.attempts = target, None, 0
        else:
            self.current_weight = self.broker.weight(quote.mid)
            self.pending, self.attempts = target, self.attempts + 1
        return result


def _migrate_state(s: dict[str, Any]) -> dict[str, Any]:
    """Journaux créés à l'étape 5 (paper uniquement) : portefeuille à plat, pas de mode ni d'ordre en attente."""
    s = dict(s)
    if "broker" not in s:
        s["broker"] = {k: s.pop(k) for k in ("cash", "units", "fees_paid", "entry")}
    s.setdefault("pending", None)
    s.setdefault("attempts", 0)
    s["identity"] = {"mode": "paper", **s["identity"]}
    return s


def make_identity(
    mode: str, exchange: str, symbol: str, timeframe: str, strategy: Strategy, risk: RiskConfig | None
) -> dict[str, Any]:
    """Ce qui doit rester identique pour reprendre un journal existant.

    Passée par JSON : un tuple (ex. fenêtres (50, 100, 150, 200)) devient une liste une fois sauvegardé ;
    sans cette normalisation, l'identité relue ne serait jamais égale et chaque redémarrage serait refusé.
    """
    identity = {
        "mode": mode,
        "exchange": exchange,
        "symbol": symbol,
        "timeframe": timeframe,
        "strategy": strategy.name,
        "params": strategy.params,
        "risk": None if risk is None else asdict(risk),
    }
    return json.loads(json.dumps(identity))
