from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import requests

from bot.monitoring import Alerts, DailySummary, NullNotifier, TelegramNotifier, check_health, notifier_from_env
from bot.realtime import Store
from bot.realtime.runner import HEARTBEAT_KEY, run_loop
from tests.test_paper import FakeClock, FakeFeed, FlakyTrader, _closes, _trader


class Outbox:
    """Notifier de test : garde les messages au lieu de les envoyer."""

    def __init__(self) -> None:
        self.messages: list[str] = []

    def send(self, text: str) -> bool:
        self.messages.append(text)
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
    assert kw["timeout"] > 0


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
    notifier = TelegramNotifier("t", "")
    assert notifier.find_chat_ids(get=lambda url, **kw: FakeResponse(payload=payload)) == [("42", "Moi")]


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
    assert "ACHAT 0.00100000 à 82,000.00" in out.messages[0] and out.messages[0].startswith("[TESTNET")
    assert "KILL SWITCH" in out.messages[1] and "CRASH" in out.messages[2]


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
    assert len(out.messages) == 1
    text = out.messages[0]
    assert "Résumé du 2021-01-01" in text and "Buy & hold" in text and "Ordres du jour : 1" in text


def test_daily_summary_not_duplicated_after_restart(tmp_path: Path) -> None:
    store, out, daily = _run_days(tmp_path)
    daily.maybe_send(pd.Timestamp("2021-01-01 12:00", tz="UTC"))
    daily.maybe_send(pd.Timestamp("2021-01-02 00:01", tz="UTC"))
    again = DailySummary(store, out, "PAPER")  # même journal, nouvel objet (= redémarrage)
    assert again.maybe_send(pd.Timestamp("2021-01-02 09:00", tz="UTC")) is False
    assert len(out.messages) == 1


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
