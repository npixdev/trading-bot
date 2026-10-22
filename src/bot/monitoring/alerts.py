"""Quoi envoyer, et quand : alertes immédiates, alertes limitées en fréquence, résumé quotidien,
commandes Telegram en lecture seule (/status, /aide).

Couleurs : Telegram n'affiche pas de texte coloré ; on utilise des pastilles emoji, toujours
accompagnées d'un mot (« Achat », « Kill switch »…) pour ne jamais reposer sur la couleur seule.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from bot.monitoring.charts import equity_chart
from bot.monitoring.notifier import Notifier, TelegramNotifier, escape
from bot.realtime.status import summarize
from bot.realtime.store import Store

log = logging.getLogger(__name__)

# Envoyées tout de suite : chacune demande ton attention.
IMMEDIATE = {
    "kill_switch": "🛑 <b>KILL SWITCH — trading arrêté</b>",
    "daily_limit": "⏸️ <b>Limite de perte journalière</b> — pause jusqu'à minuit UTC",
    "stop": "🔻 <b>Stop-loss déclenché</b>",
    "crash": "💥 <b>CRASH — le bot s'est arrêté</b>",
    "order_rejected": "⚠️ <b>Ordre rejeté</b>",
    "order_gave_up": "⚠️ <b>Ordre abandonné</b>",
    "order_unconfirmed": "⚠️ <b>Ordre non confirmé</b>",
    "order_recovered": "ℹ️ <b>Ordre retrouvé</b> après une coupure",
    "reconcile": "⚠️ <b>Écart avec le solde réel</b>",
    "resume": "ℹ️ <b>Reprise après un arrêt</b>",
    "history_short": "⚠️ <b>Historique insuffisant</b>",
    "run_start": "✅ <b>Bot démarré</b>",
    "run_end": "⏹️ <b>Bot arrêté</b>",
}
# Peuvent se répéter en rafale (panne de l'exchange…) : au plus une alerte par type et par fenêtre.
THROTTLED = {
    "network_error": "📡 <b>Erreur réseau</b>",
    "stream_error": "🔌 <b>Flux de prix temps réel coupé</b>",
    "clock_resync": "🕐 <b>Horloge resynchronisée</b>",
    "partial_fill": "◐ <b>Exécution partielle</b>",
    "order_unfilled": "○ <b>Ordre non exécuté</b>",
    "order_too_small": "· <b>Ordre sous le minimum</b>",
}


def _money(v: float) -> str:
    return f"{v:,.2f}".replace(",", " ")


def _pct(v: float) -> str:
    return f"{v:+.2%}".replace(".", ",")


def _trend(v: float) -> str:
    return "🟢" if v > 0.00005 else "🔴" if v < -0.00005 else "⚪"


def _gauge(ratio: float, width: int = 10) -> str:
    """Jauge texte : ▰▰▰▱▱▱▱▱▱▱ 30 %."""
    filled = round(max(0.0, min(1.0, ratio)) * width)
    return "▰" * filled + "▱" * (width - filled)


def signal_text(store: Store) -> str | None:
    """« À quelle distance est-on d'un trade ? » — pour les stratégies qui s'expliquent (filtre de tendance)."""
    info = (store.load_state() or {}).get("signal_info")
    if not info or info.get("kind") != "trend":
        return None
    if not info.get("ready"):
        return "🎯 Pas encore assez d'historique pour choisir la moyenne : en cash."
    raw = store.get_meta("last_price")
    price = json.loads(raw)["price"] if raw else info["close"]
    level, window = info["level"], info["window"]
    distance = price / level - 1
    next_choice = pd.Timestamp(info["next_reselect"])
    if info["invested"]:
        action = (f"🎯 <b>Investi</b> : prix {_money(price)}, {_pct(distance)} au-dessus de la moyenne {window} j "
                  f"({_money(level)}).\n→ Vente si une clôture journalière passe sous ≈ {_money(level)} "
                  f"({_pct(-distance / (1 + distance))} d'ici).")
    else:
        action = (f"🎯 <b>En cash</b> : prix {_money(price)}, {_pct(distance)} sous la moyenne {window} j "
                  f"({_money(level)}).\n→ Achat si une clôture journalière repasse au-dessus de ≈ {_money(level)} "
                  f"({_pct(-distance / (1 + distance))} d'ici).")
    return f"{action}\n🗓️ Prochain choix de la moyenne : à la clôture du {next_choice:%d/%m/%Y} (minuit UTC)."


def status_text(store: Store, label: str, heading: str = "📊 <b>État du bot</b>") -> str | None:
    s = summarize(store)
    if s is None:
        return None
    signal = signal_text(store)
    quote = s["identity"]["symbol"].split("/")[-1]
    edge = s["return"] - s["buy_hold_return"]
    guard = "🛑 kill switch ACTIF" if s["killed"] else "⏸️ pause journalière" if s["halted_day"] else "✅ actifs"
    stop = f"stop à {_money(s['stop'])}" if s["stop"] else "aucun stop en cours"
    return (
        f"{heading}\n<i>{escape(label)}</i>\n\n"
        f"💰 <b>{_money(s['equity'])} {escape(quote)}</b>  {_trend(s['return'])} {_pct(s['return'])} depuis le début\n"
        f"📈 Buy &amp; hold : {_trend(s['buy_hold_return'])} {_pct(s['buy_hold_return'])}  "
        f"(écart {_pct(edge)})\n"
        f"📦 Position : {_gauge(s['position_weight'])} {s['position_weight']:.0%}\n"
        f"📉 Pire chute : {_pct(s['max_drawdown'])}\n"
        f"🧾 {s['n_orders']} ordre(s), frais {_money(s['fees_paid'])} {escape(quote)}\n"
        f"🛡️ Garde-fous {guard} — {stop}"
        + (f"\n\n{signal}" if signal else "")
    )


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

    def _footer(self) -> str:
        return f"\n<i>{escape(self.label)}</i>"

    def _order(self, o: dict[str, Any]) -> None:
        buy = o["side"] == "buy"
        head = "🟢 <b>ACHAT</b>" if buy else "🔴 <b>VENTE</b>"
        reason = {"signal": "signal de la stratégie", "stop": "stop-loss", "retry": "nouvel essai",
                  "exposure": "réduction de l'exposition"}.get(o["reason"], o["reason"])
        self.notifier.send(
            f"{head}  {o['qty']:.8f}\n"
            f"Prix : <b>{_money(o['price'])}</b>\n"
            f"Montant : {_money(o['qty'] * o['price'])} · frais {o['fee']:,.4f}\n"
            f"Motif : {escape(reason)}{self._footer()}"
        )

    def _event(self, kind: str, detail: str) -> None:
        if kind in IMMEDIATE:
            self.notifier.send(f"{IMMEDIATE[kind]}\n{escape(detail[:1500])}{self._footer()}")
        elif kind in THROTTLED:
            now = self.clock()
            last = self._last_sent.get(kind)
            if last is not None and now - last < self.throttle_seconds:
                self._suppressed[kind] = self._suppressed.get(kind, 0) + 1
                return
            extra = self._suppressed.pop(kind, 0)
            suffix = f"\n<i>(+{extra} similaire(s) depuis la dernière alerte)</i>" if extra else ""
            self._last_sent[kind] = now
            self.notifier.send(f"{THROTTLED[kind]}\n{escape(detail[:500])}{suffix}{self._footer()}")


def send_status(store: Store, notifier: Notifier, label: str, heading: str = "📊 <b>État du bot</b>") -> bool:
    """État + graphique (ou texte seul si le graphique n'est pas encore possible)."""
    text = status_text(store, label, heading)
    if text is None:
        return notifier.send(f"ℹ️ Pas encore de données.\n<i>{escape(label)}</i>")
    png = equity_chart(store, label)
    if png is not None:
        return notifier.send_photo(png, text)
    return notifier.send(text)


class DailySummary:
    """Un résumé par jour UTC (texte + graphique), envoyé au premier tour de boucle du jour suivant.

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
        self.store.set_meta(self.META_KEY, today.isoformat())
        self.store.commit()
        send_status(self.store, self.notifier, self.label, self.heading(last_day))
        return True

    def heading(self, day: pd.Timestamp) -> str:
        nxt = day + pd.Timedelta(days=1)
        orders = self.store.table("orders")
        n_orders = int(((orders["time"] >= day) & (orders["time"] < nxt)).sum()) if len(orders) else 0
        equity = self.store.table("equity")
        before = equity[equity["time"] < day]
        during = equity[(equity["time"] >= day) & (equity["time"] < nxt)]
        start = float(before["equity"].iloc[-1]) if len(before) else (float(during["equity"].iloc[0]) if len(during) else None)
        end = float(during["equity"].iloc[-1]) if len(during) else start
        day_ret = (end / start - 1) if start else 0.0
        return (f"🗓️ <b>Résumé du {day:%d/%m/%Y}</b>\n"
                f"Journée : {_trend(day_ret)} {_pct(day_ret)} · {n_orders} ordre(s)")


HELP = (
    "🤖 <b>Commandes disponibles</b>\n"
    "/status — état du bot + graphique\n"
    "/aide — cette aide\n\n"
    "<i>Le bot répond seulement quand il est en marche (en moins d'une minute).\n"
    "Lecture seule : aucune commande ne peut faire trader le bot.</i>"
)
# Tolérant aux fautes de frappe et aux oublis de « / » : un débutant ne doit pas rester sans réponse.
STATUS_ALIASES = {"status", "stats", "stat", "statut", "etat", "état", "graphique", "graph"}
HELP_ALIASES = {"aide", "help", "start", "commandes", "menu"}


class TelegramCommands:
    """Répond à /status et /aide, uniquement depuis la conversation configurée (TELEGRAM_CHAT_ID).

    Lecture seule par conception : aucune commande ne modifie l'état du bot ni ne passe d'ordre.
    """

    META_KEY = "telegram_offset"

    def __init__(self, notifier: Notifier, store: Store, label: str, min_interval: float = 10.0,
                 clock: Callable[[], float] = time.monotonic, max_age_seconds: float = 300.0) -> None:
        self.notifier, self.store, self.label = notifier, store, label
        self.min_interval, self.clock, self.max_age_seconds = min_interval, clock, max_age_seconds
        self._last_poll: float | None = None

    def poll(self, now: pd.Timestamp | None = None, wait: int = 0) -> int:
        """Lit et traite les commandes en attente. Renvoie le nombre de commandes exécutées,
        ou -1 si Telegram est injoignable.

        `wait` > 0 : attente longue côté Telegram (utilisé par le fil d'écoute TelegramListener).
        """
        if not isinstance(self.notifier, TelegramNotifier):
            return 0
        t = self.clock()
        if self._last_poll is not None and t - self._last_poll < self.min_interval:
            return 0
        self._last_poll = t
        offset = self.store.get_meta(self.META_KEY)
        updates = self.notifier.get_updates(int(offset) if offset is not None else None, wait=wait)
        if updates is None:
            return -1
        return self.handle(updates, now or pd.Timestamp.now(tz="UTC"))

    def handle(self, updates: list[dict[str, Any]], now: pd.Timestamp) -> int:
        handled = 0
        for update in updates:
            self.store.set_meta(self.META_KEY, str(int(update["update_id"]) + 1))  # ne jamais retraiter
            message = update.get("message") or {}
            if str((message.get("chat") or {}).get("id")) != str(self.notifier.chat_id):
                log.warning("Commande Telegram ignorée : conversation non autorisée")
                continue
            sent = pd.Timestamp(int(message.get("date", 0)), unit="s", tz="UTC")
            if (now - sent).total_seconds() > self.max_age_seconds:
                # Envoyée pendant que le bot était arrêté : y répondre maintenant serait trompeur.
                log.info("Commande Telegram périmée ignorée (envoyée le %s)", sent)
                continue
            text = (message.get("text") or "").strip()
            command = text.split("@")[0].split(" ")[0].lower().lstrip("/")
            if command in STATUS_ALIASES:
                self.notifier.send_chat_action("upload_photo")  # « envoie une photo… » s'affiche tout de suite
                send_status(self.store, self.notifier, self.label)
            elif command in HELP_ALIASES:
                self.notifier.send(HELP)
            else:
                self.notifier.send(f"🤔 Je n'ai pas compris « {escape(text[:50])} ».\n\n{HELP}")
            handled += 1
        self.store.commit()
        return handled


class TelegramListener(threading.Thread):
    """Écoute Telegram en continu, à côté de la boucle de trading : réponse en 1 à 3 s au lieu
    d'attendre le prochain tour de boucle (jusqu'à 60 s).

    Fil séparé avec **sa propre connexion** au journal (SQLite interdit de partager une connexion
    entre fils). En lecture seule pour le trading : il ne touche qu'à l'offset des commandes.
    Une erreur (réseau, bug) est journalisée puis l'écoute reprend ; elle ne peut pas arrêter le trading.
    """

    def __init__(self, notifier: Notifier, db_path: Path, label: str, wait: int = 25,
                 max_backoff: float = 60.0) -> None:
        super().__init__(name="telegram-listener", daemon=True)  # daemon : ne bloque pas l'arrêt du bot
        self.notifier, self.db_path, self.label, self.wait = notifier, db_path, label, wait
        self.max_backoff = max_backoff
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        if not isinstance(self.notifier, TelegramNotifier):
            return
        store = Store(self.db_path)
        commands = TelegramCommands(self.notifier, store, self.label, min_interval=0)
        backoff = 0.0
        try:
            while not self._stop_event.is_set():
                try:
                    result = commands.poll(wait=self.wait)
                except Exception:  # noqa: BLE001 — l'écoute ne doit jamais mourir
                    log.exception("Erreur dans l'écoute des commandes Telegram")
                    result = -1
                if result >= 0:
                    if backoff:
                        log.info("Écoute Telegram rétablie")
                    backoff = 0.0
                    continue
                # Telegram injoignable (réveil de veille, coupure Internet…) : on attend de plus en plus
                # longtemps au lieu de réessayer en boucle (vécu : 300 essais en 0,5 s au réveil du PC).
                backoff = min(max(2.0, backoff * 2), self.max_backoff)
                if backoff == 2.0:
                    log.warning("Telegram injoignable : nouvel essai dans %.0f s (puis de plus en plus espacé)", backoff)
                self._stop_event.wait(backoff)
        finally:
            store.close()
