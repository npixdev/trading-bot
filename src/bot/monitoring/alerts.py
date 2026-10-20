"""Quoi envoyer, et quand : alertes immédiates, alertes limitées en fréquence, résumé quotidien."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import pandas as pd

from bot.monitoring.notifier import Notifier
from bot.realtime.status import summarize
from bot.realtime.store import Store

# Envoyées tout de suite : chacune demande ton attention.
IMMEDIATE = {
    "kill_switch": "🛑 KILL SWITCH",
    "daily_limit": "⏸ Limite journalière",
    "stop": "🔻 Stop-loss",
    "crash": "💥 CRASH — bot arrêté",
    "order_rejected": "⚠ Ordre rejeté",
    "order_gave_up": "⚠ Ordre abandonné",
    "order_unconfirmed": "⚠ Ordre non confirmé",
    "order_recovered": "ℹ Ordre retrouvé après coupure",
    "reconcile": "⚠ Écart avec le solde réel",
    "resume": "ℹ Reprise après arrêt",
    "run_start": "▶ Bot démarré",
    "run_end": "⏹ Bot arrêté",
}
# Peuvent se répéter en rafale (panne de l'exchange…) : au plus une alerte par type et par fenêtre.
THROTTLED = {
    "network_error": "📡 Erreur réseau",
    "clock_resync": "🕐 Horloge resynchronisée",
    "partial_fill": "◐ Exécution partielle",
    "order_unfilled": "○ Ordre non exécuté",
    "order_too_small": "· Ordre sous le minimum",
}


class Alerts:
    """Écoute le journal (Store) et transforme ordres et événements en messages."""

    def __init__(
        self,
        notifier: Notifier,
        label: str,
        throttle_seconds: float = 1800,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.notifier, self.label = notifier, label
        self.throttle_seconds, self.clock = throttle_seconds, clock
        self._last_sent: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def __call__(self, record: str, data: dict[str, Any]) -> None:
        if record == "order":
            self._order(data)
        elif record == "event":
            self._event(data["kind"], data["detail"])

    def _order(self, o: dict[str, Any]) -> None:
        verb = "ACHAT" if o["side"] == "buy" else "VENTE"
        self.notifier.send(
            f"[{self.label}] {verb} {o['qty']:.8f} à {o['price']:,.2f} "
            f"(montant {o['qty'] * o['price']:,.2f}, frais {o['fee']:,.4f}) — {o['reason']}"
        )

    def _event(self, kind: str, detail: str) -> None:
        if kind in IMMEDIATE:
            self.notifier.send(f"[{self.label}] {IMMEDIATE[kind]}\n{detail[:1500]}")
        elif kind in THROTTLED:
            now = self.clock()
            last = self._last_sent.get(kind)
            if last is not None and now - last < self.throttle_seconds:
                self._suppressed[kind] = self._suppressed.get(kind, 0) + 1
                return
            extra = self._suppressed.pop(kind, 0)
            suffix = f"\n(+{extra} similaire(s) depuis la dernière alerte)" if extra else ""
            self._last_sent[kind] = now
            self.notifier.send(f"[{self.label}] {THROTTLED[kind]}\n{detail[:500]}{suffix}")


class DailySummary:
    """Un résumé par jour UTC, envoyé au premier tour de boucle du jour suivant.

    La date du dernier résumé est stockée dans le journal : un redémarrage n'envoie pas de doublon.
    """

    META_KEY = "last_summary_day"

    def __init__(self, store: Store, notifier: Notifier, label: str) -> None:
        self.store, self.notifier, self.label = store, notifier, label

    def maybe_send(self, now: pd.Timestamp) -> bool:
        today = now.normalize()
        last = self.store.get_meta(self.META_KEY)
        if last is None:
            self.store.set_meta(self.META_KEY, today.isoformat())  # pas de résumé pour un jour incomplet
            self.store.commit()
            return False
        last_day = pd.Timestamp(last)
        if today <= last_day:
            return False
        text = self.build(last_day)
        self.store.set_meta(self.META_KEY, today.isoformat())
        self.store.commit()
        if text:
            self.notifier.send(text)
        return True

    def build(self, day: pd.Timestamp) -> str | None:
        summary = summarize(self.store)
        if summary is None:
            return None
        nxt = day + pd.Timedelta(days=1)
        equity = self.store.table("equity")
        orders = self.store.table("orders")
        events = self.store.table("events")
        before = equity[equity["time"] < day]
        during = equity[(equity["time"] >= day) & (equity["time"] < nxt)]
        start_eq = float(before["equity"].iloc[-1]) if len(before) else (float(during["equity"].iloc[0]) if len(during) else None)
        end_eq = float(during["equity"].iloc[-1]) if len(during) else start_eq
        day_ret = (end_eq / start_eq - 1) if start_eq else 0.0
        n_orders = int(((orders["time"] >= day) & (orders["time"] < nxt)).sum()) if len(orders) else 0
        day_events = events[(events["time"] >= day) & (events["time"] < nxt)] if len(events) else events
        notable = day_events[~day_events["kind"].isin(["run_start", "run_end", "start"])]["kind"].value_counts().to_dict() if len(day_events) else {}
        return (
            f"[{self.label}] 📊 Résumé du {day:%Y-%m-%d} (UTC)\n"
            f"Portefeuille : {summary['equity']:,.2f} ({day_ret:+.2%} sur la journée, {summary['return']:+.2%} depuis le début)\n"
            f"Buy & hold depuis le début : {summary['buy_hold_return']:+.2%}\n"
            f"Position : {summary['position_weight']:.0%} investi | drawdown max {summary['max_drawdown']:+.2%}\n"
            f"Ordres du jour : {n_orders} | frais cumulés : {summary['fees_paid']:,.2f}\n"
            f"Événements du jour : {notable or 'aucun'}\n"
            f"Kill switch : {'ACTIF' if summary['killed'] else 'non'}"
        )
