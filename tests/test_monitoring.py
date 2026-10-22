from __future__ import annotations

import threading
import time
from pathlib import Path

import pandas as pd
import pytest
import requests

from bot.monitoring import (
    Alerts,
    DailySummary,
    NullNotifier,
    TelegramCommands,
    TelegramListener,
    TelegramNotifier,
    check_health,
    notifier_from_env,
)
from bot.monitoring.alerts import status_text
from bot.monitoring.charts import equity_chart
from bot.realtime import Store
from bot.realtime.runner import HEARTBEAT_KEY, run_loop
from tests.test_paper import FakeClock, FakeFeed, FlakyTrader, _closes, _trader


class Outbox:
    """Notifier de test : garde les messages au lieu de les envoyer."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.photos: list[tuple[bytes, str]] = []

    def send(self, text: str) -> bool:
        self.messages.append(text)
        return True

    def send_photo(self, png: bytes, caption: str = "") -> bool:
        self.photos.append((png, caption))
        return True

    def send_chat_action(self, action: str = "upload_photo") -> bool:
        return True


class FakeResponse:
    def __init__(self, status: int = 200, payload: dict | None = None) -> None:
        self.status_code, self.text, self._payload = status, "", payload or {}

    def json(self) -> dict:
        return self._payload


# --- Telegram -----------------------------------------------------------------------------------


def test_telegram_posts_message_to_chat() -> None:
    calls = []
    notifier = TelegramNotifier("123:ABC", "42", post=lambda url, **kw: calls.append((url, kw)) or FakeResponse())
    assert notifier.send("bonjour")
    url, kw = calls[0]
    assert url == "https://api.telegram.org/bot123:ABC/sendMessage"
    assert kw["json"]["chat_id"] == "42" and kw["json"]["text"] == "bonjour"
    assert kw["json"]["parse_mode"] == "HTML"
    assert kw["timeout"] > 0


def test_telegram_sends_photo_with_caption() -> None:
    calls = []
    notifier = TelegramNotifier("t", "42", post=lambda url, **kw: calls.append((url, kw)) or FakeResponse())
    assert notifier.send_photo(b"\x89PNG...", "légende " * 300)
    url, kw = calls[0]
    assert url.endswith("/sendPhoto")
    assert kw["files"]["photo"][1] == b"\x89PNG..." and kw["data"]["chat_id"] == "42"
    assert len(kw["data"]["caption"]) <= 1024


def test_dynamic_text_is_escaped_for_telegram_html() -> None:
    out = Outbox()
    Alerts(out, "PAPER")("event", {"time": None, "kind": "order_rejected", "detail": "erreur <code> & x>1"})
    assert "&lt;code&gt; &amp; x&gt;1" in out.messages[0]  # sinon Telegram refuserait le message


def test_telegram_failure_never_raises_nor_leaks_token(caplog) -> None:
    def boom(url, **kw):
        raise requests.ConnectionError(f"impossible de joindre {url}")

    notifier = TelegramNotifier("123:SECRET", "42", post=boom)
    assert notifier.send("x") is False
    assert "SECRET" not in caplog.text and "SECRET" not in repr(notifier)
    refused = TelegramNotifier("123:SECRET", "42", post=lambda url, **kw: FakeResponse(status=401))
    assert refused.send("x") is False


def test_long_messages_are_truncated() -> None:
    sent = []
    TelegramNotifier("t", "c", post=lambda url, **kw: sent.append(kw["json"]["text"]) or FakeResponse()).send("x" * 10_000)
    assert len(sent[0]) <= 4096


def test_find_chat_ids_from_updates() -> None:
    payload = {"result": [{"message": {"chat": {"id": 42, "first_name": "Moi"}}}, {"message": {"chat": {"id": 42}}}]}
    notifier = TelegramNotifier("t", "", get=lambda url, **kw: FakeResponse(payload=payload))
    assert notifier.find_chat_ids() == [("42", "Moi")]


def test_notifier_from_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    env = tmp_path / ".env"
    env.write_text("")
    assert isinstance(notifier_from_env(env), NullNotifier)  # non configuré : le bot tourne quand même
    env.write_text("TELEGRAM_BOT_TOKEN=1:abc\nTELEGRAM_CHAT_ID=7\n")
    assert isinstance(notifier_from_env(env), TelegramNotifier)


# --- Règles d'alerte ------------------------------------------------------------------------------


def test_orders_and_critical_events_are_sent_immediately() -> None:
    out = Outbox()
    alerts = Alerts(out, "TESTNET binance BTC/USDT 1m")
    alerts("order", {"side": "buy", "qty": 0.001, "price": 82_000.0, "fee": 0.08, "reason": "signal"})
    alerts("event", {"time": None, "kind": "kill_switch", "detail": "drawdown -15,2 %"})
    alerts("event", {"time": None, "kind": "crash", "detail": "ZeroDivisionError"})
    assert len(out.messages) == 3
    buy = out.messages[0]
    assert buy.startswith("🟢 <b>ACHAT</b>") and "82 000.00" in buy and "TESTNET binance" in buy
    assert "signal de la stratégie" in buy
    assert "🛑" in out.messages[1] and "KILL SWITCH" in out.messages[1]
    assert "💥" in out.messages[2] and "CRASH" in out.messages[2]
    alerts = Alerts(out, "X")
    alerts("order", {"side": "sell", "qty": 0.001, "price": 81_000.0, "fee": 0.08, "reason": "stop"})
    assert out.messages[-1].startswith("🔴 <b>VENTE</b>") and "stop-loss" in out.messages[-1]


def test_repeated_network_errors_are_throttled() -> None:
    out, clock = Outbox(), FakeClock()
    alerts = Alerts(out, "PAPER", throttle_seconds=1800, clock=clock)
    for _ in range(50):  # panne de l'exchange : une erreur toutes les 20 s pendant ~17 min
        alerts("event", {"time": None, "kind": "network_error", "detail": "timeout"})
        clock.sleep(20)
    assert len(out.messages) == 1
    clock.sleep(1800)
    alerts("event", {"time": None, "kind": "network_error", "detail": "timeout"})
    assert len(out.messages) == 2 and "+49 similaire(s)" in out.messages[1]


def test_routine_events_are_not_sent() -> None:
    out = Outbox()
    alerts = Alerts(out, "PAPER")
    alerts("event", {"time": None, "kind": "start", "detail": "démarrage"})
    assert out.messages == []


def test_alerts_flow_from_trader_through_store(tmp_path: Path) -> None:
    out = Outbox()
    feed = FakeFeed(_closes())
    trader = _trader(feed, tmp_path / "p.sqlite")
    trader.store.listener = Alerts(out, "PAPER")
    feed.advance(5)  # signal d'achat
    trader.step()
    assert any("ACHAT" in m for m in out.messages)


def test_broken_alert_never_breaks_trading(tmp_path: Path) -> None:
    def broken(record, data):
        raise RuntimeError("bug dans les alertes")

    feed = FakeFeed(_closes())
    trader = _trader(feed, tmp_path / "p.sqlite")
    trader.store.listener = broken
    feed.advance(5)
    trader.step()  # ne doit pas lever
    assert trader.broker.units > 0


# --- Résumé quotidien --------------------------------------------------------------------------------


def _run_days(tmp_path: Path) -> tuple[Store, Outbox, DailySummary]:
    feed = FakeFeed(_closes())
    trader = _trader(feed, tmp_path / "p.sqlite")
    for i in range(4, 10):
        feed.advance(i)
        trader.step()
    out = Outbox()
    return trader.store, out, DailySummary(trader.store, out, "PAPER")


def test_daily_summary_sent_once_per_day(tmp_path: Path) -> None:
    store, out, daily = _run_days(tmp_path)
    day1 = pd.Timestamp("2021-01-01 12:00", tz="UTC")
    assert daily.maybe_send(day1) is False  # premier jour : rien (journée incomplète)
    assert daily.maybe_send(day1 + pd.Timedelta(hours=5)) is False
    assert daily.maybe_send(pd.Timestamp("2021-01-02 00:01", tz="UTC")) is True
    assert daily.maybe_send(pd.Timestamp("2021-01-02 18:00", tz="UTC")) is False
    assert len(out.photos) == 1 and out.messages == []  # graphique + légende, en un seul message
    png, caption = out.photos[0]
    assert png.startswith(b"\x89PNG")
    assert "Résumé du 01/01/2021" in caption and "Buy &amp; hold" in caption and "1 ordre(s)" in caption


def test_daily_summary_not_duplicated_after_restart(tmp_path: Path) -> None:
    store, out, daily = _run_days(tmp_path)
    daily.maybe_send(pd.Timestamp("2021-01-01 12:00", tz="UTC"))
    daily.maybe_send(pd.Timestamp("2021-01-02 00:01", tz="UTC"))
    again = DailySummary(store, out, "PAPER")  # même journal, nouvel objet (= redémarrage)
    assert again.maybe_send(pd.Timestamp("2021-01-02 09:00", tz="UTC")) is False
    assert len(out.photos) == 1


# --- Graphique et commandes ---------------------------------------------------------------------------


def test_equity_chart_is_a_png_with_orders(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    png = equity_chart(store, "PAPER fake BTC/USDC 1h")
    assert png is not None and png.startswith(b"\x89PNG") and len(png) > 10_000


def test_equity_chart_needs_data(tmp_path: Path) -> None:
    assert equity_chart(Store(tmp_path / "vide.sqlite"), "x") is None


def test_status_text_is_readable(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    text = status_text(store, "PAPER")
    assert "💰" in text and "Buy &amp; hold" in text and "▰" in text and "Garde-fous" in text


class FakeTelegram(TelegramNotifier):
    """Vrai TelegramNotifier, mais réseau simulé : messages entrants programmables, sorties capturées."""

    def __init__(self, chat_id: str = "42") -> None:
        super().__init__("t", chat_id)
        self.inbox: list[dict] = []
        self.texts: list[str] = []
        self.photos: list[str] = []
        self.actions: list[str] = []

    def send_chat_action(self, action: str = "upload_photo") -> bool:
        self.actions.append(action)
        return True

    def get_updates(self, offset=None, wait=0):
        found = [u for u in self.inbox if offset is None or u["update_id"] >= offset]
        if not found and wait:
            time.sleep(0.05)  # simule l'attente longue de Telegram (sans bloquer le test 25 s)
        return found

    def send(self, text: str) -> bool:
        self.texts.append(text)
        return True

    def send_photo(self, png: bytes, caption: str = "") -> bool:
        self.photos.append(caption)
        return True


def _msg(update_id: int, text: str, chat: str = "42", age_seconds: float = 5) -> dict:
    date = int(pd.Timestamp.now(tz="UTC").timestamp() - age_seconds)
    return {"update_id": update_id, "message": {"chat": {"id": int(chat)}, "text": text, "date": date}}


def test_stale_commands_sent_while_bot_was_down_are_ignored(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    tg = FakeTelegram()
    tg.inbox = [_msg(1, "/status", age_seconds=3 * 3600), _msg(2, "/status", age_seconds=10)]
    assert TelegramCommands(tg, store, "PAPER", min_interval=0).poll() == 1  # seule la récente obtient une réponse
    assert len(tg.photos) == 1


def test_status_command_replies_with_chart(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    tg = FakeTelegram()
    tg.inbox = [_msg(1, "/status"), _msg(2, "/aide")]
    commands = TelegramCommands(tg, store, "PAPER", min_interval=0)
    assert commands.poll() == 2
    assert len(tg.photos) == 1 and "État du bot" in tg.photos[0]
    assert "Commandes disponibles" in tg.texts[0]
    assert commands.poll() == 0  # déjà traitées : l'offset est mémorisé dans le journal
    again = TelegramCommands(tg, store, "PAPER", min_interval=0)  # après redémarrage
    assert again.poll() == 0


def test_command_typos_and_missing_slash_still_work(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    tg = FakeTelegram()
    tg.inbox = [_msg(1, "/stats"), _msg(2, "status"), _msg(3, "/Statut@trading_nmz_bot")]
    assert TelegramCommands(tg, store, "PAPER", min_interval=0).poll() == 3
    assert len(tg.photos) == 3


def test_unknown_command_gets_an_answer(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    tg = FakeTelegram()
    tg.inbox = [_msg(1, "/acheter <tout>")]
    TelegramCommands(tg, store, "PAPER", min_interval=0).poll()
    assert "Je n'ai pas compris" in tg.texts[0] and "&lt;tout&gt;" in tg.texts[0]
    assert "Commandes disponibles" in tg.texts[0] and tg.photos == []  # rien d'autre ne se passe


def test_listener_answers_within_seconds_while_trading_runs(tmp_path: Path) -> None:
    """Le fil d'écoute répond sans attendre la boucle de trading, avec sa propre connexion SQLite."""
    store, _, _ = _run_days(tmp_path)
    store.commit()
    tg = FakeTelegram()
    listener = TelegramListener(tg, store.path, "PAPER", wait=1)
    listener.start()
    try:
        tg.inbox.append(_msg(1, "/status"))
        start = time.monotonic()
        # Pendant ce temps, la « boucle de trading » continue d'écrire dans le journal.
        while not tg.photos and time.monotonic() - start < 10:
            store.log_event(pd.Timestamp.now(tz="UTC"), "start", "écriture concurrente")
            store.commit()
            time.sleep(0.05)
        elapsed = time.monotonic() - start
    finally:
        listener.stop()
        listener.join(timeout=5)
    assert len(tg.photos) == 1 and elapsed < 5
    assert "typing" not in tg.actions and tg.actions == ["upload_photo"]


def test_charts_can_be_drawn_from_two_threads_at_once(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    store.commit()
    results: list[bytes | None] = []

    def draw() -> None:
        own = Store(store.path)  # une connexion par fil, comme le TelegramListener
        results.append(equity_chart(own, "x"))
        own.close()

    threads = [threading.Thread(target=draw) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 3 and all(r is not None and r.startswith(b"\x89PNG") for r in results)


def test_commands_from_other_chats_are_ignored(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    tg = FakeTelegram(chat_id="42")
    tg.inbox = [_msg(1, "/status", chat="666")]  # quelqu'un d'autre a trouvé le bot
    assert TelegramCommands(tg, store, "PAPER", min_interval=0).poll() == 0
    assert tg.photos == [] and tg.texts == []


def test_commands_are_rate_limited_and_inactive_without_telegram(tmp_path: Path) -> None:
    store, _, _ = _run_days(tmp_path)
    clock = FakeClock()
    tg = FakeTelegram()
    commands = TelegramCommands(tg, store, "PAPER", min_interval=10, clock=clock)
    commands.poll()
    tg.inbox = [_msg(5, "/status")]
    assert commands.poll() == 0  # moins de 10 s : pas de nouvelle lecture
    clock.sleep(11)
    assert commands.poll() == 1
    assert TelegramCommands(NullNotifier(), store, "PAPER").poll() == 0


# --- Heartbeat ------------------------------------------------------------------------------------------


def test_health_check(tmp_path: Path) -> None:
    store = Store(tmp_path / "h.sqlite")
    now = pd.Timestamp("2026-10-20 12:00", tz="UTC")
    assert check_health(store, 600, now)[0] is False  # jamais démarré
    store.set_meta(HEARTBEAT_KEY, (now - pd.Timedelta(seconds=30)).isoformat())
    assert check_health(store, 600, now)[0] is True
    assert check_health(store, 600, now + pd.Timedelta(minutes=20))[0] is False  # bloqué


def test_runner_writes_heartbeat_and_calls_daily_hook(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    clock = FakeClock()
    seen: list[pd.Timestamp] = []
    run_loop(FlakyTrader([]), FakeFeed(_closes()), store, 10, 35, clock.sleep, clock, on_step=seen.append)
    assert store.get_meta(HEARTBEAT_KEY) is not None
    assert len(seen) == 4


def test_runner_survives_broken_daily_hook(tmp_path: Path) -> None:
    store = Store(tmp_path / "r.sqlite")
    clock = FakeClock()

    def broken(now):
        raise ValueError("bug du résumé")

    run_loop(FlakyTrader([]), FakeFeed(_closes()), store, 10, 25, clock.sleep, clock, on_step=broken)
    assert store.table("events")["kind"].iloc[-1] == "run_end"  # la boucle a continué jusqu'au bout


def _code(path: str) -> str:
    """Contenu sans les commentaires (« # … ») : ce que Docker exécute réellement."""
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return "\n".join(line.split("#", 1)[0] for line in lines)


@pytest.mark.parametrize("name", ["Dockerfile", "docker-compose.yml", ".dockerignore"])
def test_deployment_files_exist(name: str) -> None:
    assert Path(name).exists()


def test_secrets_never_baked_into_image() -> None:
    dockerignore = Path(".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in dockerignore and "data/" in dockerignore
    dockerfile = _code("Dockerfile")
    assert ".env" not in dockerfile
    assert "USER " in dockerfile  # pas de root dans le conteneur


def test_compose_never_runs_live_unattended() -> None:
    compose = _code("docker-compose.yml")
    assert "--live" not in compose  # le réel exige une confirmation tapée : jamais automatisé
    assert "restart: unless-stopped" in compose and "healthcheck" in compose
    assert "env_file" in compose


def test_secondary_telegram_bot_uses_its_own_env_names(tmp_path: Path, monkeypatch) -> None:
    for var in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_SANDBOX_BOT_TOKEN", "TELEGRAM_SANDBOX_CHAT_ID"):
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text("TELEGRAM_BOT_TOKEN=1:main\nTELEGRAM_CHAT_ID=1\n")
    assert isinstance(notifier_from_env(env), TelegramNotifier)
    assert isinstance(notifier_from_env(env, name="SANDBOX"), NullNotifier)  # jamais le token du bot sérieux
    env.write_text("TELEGRAM_SANDBOX_BOT_TOKEN=2:sand\nTELEGRAM_SANDBOX_CHAT_ID=2\n")
    sandbox = notifier_from_env(env, name="SANDBOX")
    assert isinstance(sandbox, TelegramNotifier) and sandbox.token == "2:sand"


def test_sandbox_config_is_separate_from_serious_bot() -> None:
    from bot.config import load_config

    cfg = load_config()
    assert cfg.sandbox.db != cfg.paper.db and cfg.sandbox.timeframe == "1m"
    assert cfg.paper.strategy == "trend_adaptive"
