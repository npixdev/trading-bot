from __future__ import annotations

import math
from pathlib import Path

import ccxt
import pandas as pd
import pytest

from bot.__main__ import build_parser, cmd_trade
from bot.config import RiskConfig
from bot.execution import (
    ExchangeBroker,
    PermissionReport,
    check_api_permissions,
    create_private_exchange,
    ensure_spot_market,
    load_api_keys,
)
from bot.execution.exchange import ApiKeys
from bot.execution.live_guard import confirm_live, confirmation_phrase
from bot.realtime import Quote, Store, Trader
from bot.realtime.trader import make_identity
from tests.test_paper import Above, FakeFeed

NOW = pd.Timestamp("2026-10-20 12:00", tz="UTC")
QUOTE = Quote(bid=80_000.0, ask=80_010.0, last=80_005.0)


class FakePrivateExchange:
    """Simule l'API privée ccxt d'un exchange spot, avec des comportements d'ordre programmables."""

    id = "okx"

    def __init__(self, usdc: float = 1_000.0, btc: float = 0.0, behaviors: list[str] | None = None,
                 perm: str = "read_only,trade", ip: str = "1.2.3.4", spot: bool = True) -> None:
        self.balances = {"BTC": btc, "USDC": usdc}
        self.behaviors = behaviors or []
        self.perm, self.ip, self.spot = perm, ip, spot
        self.created: list[dict] = []
        self.orders: dict[str, dict] = {}

    # Marché
    def load_markets(self) -> None:
        pass

    def market(self, symbol: str) -> dict:
        return {
            "symbol": symbol, "base": "BTC", "quote": "USDC", "spot": self.spot, "contract": not self.spot,
            "limits": {"amount": {"min": 0.0001}, "cost": {"min": 1.0}},
        }

    def amount_to_precision(self, symbol: str, amount: float) -> str:
        return f"{math.floor(amount * 1e8) / 1e8:.8f}"

    def price_to_precision(self, symbol: str, price: float) -> str:
        return f"{round(price, 1):.1f}"

    # Compte
    def fetch_balance(self) -> dict:
        return {
            "BTC": {"total": self.balances["BTC"], "free": self.balances["BTC"]},
            "USDC": {"total": self.balances["USDC"], "free": self.balances["USDC"]},
        }

    def private_get_account_config(self) -> dict:
        return {"data": [{"perm": self.perm, "ip": self.ip, "acctLv": "1"}]}

    # Ordres
    def create_order(self, symbol, type, side, amount, price, params):  # noqa: A002
        behavior = self.behaviors.pop(0) if self.behaviors else "fill"
        call = dict(symbol=symbol, type=type, side=side, amount=amount, price=price, params=dict(params))
        self.created.append(call)
        if behavior == "reject":
            raise ccxt.InsufficientFunds("solde insuffisant")
        if behavior == "network_lost":
            raise ccxt.RequestTimeout("timeout")
        fraction = {"fill": 1.0, "partial": 0.4, "unfilled": 0.0, "network_placed": 1.0}[behavior]
        filled = float(self.amount_to_precision(symbol, amount * fraction))
        fill_price = price  # au pire, le prix plafond
        if side == "buy":
            fee_btc = filled * 0.001
            self.balances["USDC"] -= filled * fill_price
            self.balances["BTC"] += filled - fee_btc
            fee = {"cost": fee_btc, "currency": "BTC"}
        else:
            proceeds = filled * fill_price
            self.balances["BTC"] -= filled
            self.balances["USDC"] += proceeds * 0.999
            fee = {"cost": proceeds * 0.001, "currency": "USDC"}
        order = {
            "id": f"ex{len(self.created)}", "clientOrderId": params["clientOrderId"], "status": "closed" if filled else "canceled",
            "filled": filled, "amount": amount, "average": fill_price if filled else None, "price": price, "fee": fee,
        }
        self.orders[order["id"]] = order
        if behavior == "network_placed":
            raise ccxt.NetworkError("connexion coupée après envoi")
        return order

    def fetch_order(self, id, symbol, params=None):  # noqa: A002
        cid = (params or {}).get("clientOrderId")
        for order in self.orders.values():
            if (cid and order["clientOrderId"] == cid) or order["id"] == id:
                return order
        raise ccxt.OrderNotFound("introuvable")

    def fetch_ticker(self, symbol: str) -> dict:
        return {"last": QUOTE.last, "bid": QUOTE.bid, "ask": QUOTE.ask}


def _broker(ex: FakePrivateExchange, max_capital: float | None = None) -> ExchangeBroker:
    return ExchangeBroker(ex, "BTC/USDC", max_capital, max_slippage=0.002, order_timeout=0.1, sleep=lambda s: None)


# --- Ordres ------------------------------------------------------------------------------------


def test_buy_is_spot_ioc_limit_capped_at_ask_plus_slippage() -> None:
    ex = FakePrivateExchange(usdc=1_000.0)
    res = _broker(ex).rebalance(NOW, QUOTE, 1.0, "signal")
    call = ex.created[0]
    assert call["type"] == "limit" and call["side"] == "buy"
    assert call["params"]["timeInForce"] == "IOC"
    assert call["params"]["tdMode"] == "cash"  # spot, aucune marge
    assert call["price"] == pytest.approx(round(80_010 * 1.002, 1))
    assert call["amount"] * call["price"] <= 1_000.0  # jamais plus que le cash disponible
    assert res.done and len(res.orders) == 1


def test_binance_orders_do_not_carry_okx_parameters() -> None:
    ex = FakePrivateExchange(usdc=1_000.0)
    ex.id = "binance"
    _broker(ex).rebalance(NOW, QUOTE, 1.0, "signal")
    params = ex.created[0]["params"]
    assert "tdMode" not in params  # Binance rejetterait un paramètre inconnu
    assert params["timeInForce"] == "IOC" and params["clientOrderId"].startswith("bot")


def test_sell_everything_at_bid_minus_slippage() -> None:
    ex = FakePrivateExchange(usdc=0.0, btc=0.01234567)
    res = _broker(ex).rebalance(NOW, QUOTE, 0.0, "signal")
    call = ex.created[0]
    assert call["side"] == "sell" and call["amount"] == 0.01234567
    assert call["price"] == pytest.approx(round(80_000 * 0.998, 1))
    assert res.done and ex.balances["BTC"] == pytest.approx(0)


def test_amount_is_floored_to_exchange_precision() -> None:
    ex = FakePrivateExchange(usdc=0.0, btc=0.123456789)
    _broker(ex).rebalance(NOW, QUOTE, 0.0, "signal")
    assert ex.created[0]["amount"] == 0.12345678  # tronqué, jamais arrondi au-dessus du solde


def test_order_below_exchange_minimum_is_skipped() -> None:
    ex = FakePrivateExchange(usdc=5.0)  # 5 USDC → 0,00006 BTC < 0,0001 minimum
    res = _broker(ex).rebalance(NOW, QUOTE, 1.0, "signal")
    assert ex.created == []
    assert res.done and res.events[0][0] == "order_too_small"


def test_partial_fill_is_reported() -> None:
    ex = FakePrivateExchange(usdc=1_000.0, behaviors=["partial"])
    res = _broker(ex).rebalance(NOW, QUOTE, 1.0, "signal")
    assert not res.done
    assert res.events[0][0] == "partial_fill"
    assert res.orders[0]["qty"] == pytest.approx(ex.created[0]["amount"] * 0.4, abs=1e-8)


def test_rejected_order_does_not_crash() -> None:
    ex = FakePrivateExchange(usdc=1_000.0, behaviors=["reject"])
    res = _broker(ex).rebalance(NOW, QUOTE, 1.0, "signal")
    assert not res.done and res.orders == []
    assert res.events[0][0] == "order_rejected"


def test_network_error_after_send_recovers_order_without_duplicate() -> None:
    """Le cas dangereux : l'ordre est parti mais la réponse s'est perdue. On ne doit PAS racheter."""
    ex = FakePrivateExchange(usdc=1_000.0, behaviors=["network_placed"])
    res = _broker(ex).rebalance(NOW, QUOTE, 1.0, "signal")
    assert len(ex.created) == 1  # un seul ordre envoyé
    assert res.done and len(res.orders) == 1
    assert res.events[0][0] == "order_recovered"


def test_network_error_before_send_is_unconfirmed() -> None:
    ex = FakePrivateExchange(usdc=1_000.0, behaviors=["network_lost"])
    res = _broker(ex).rebalance(NOW, QUOTE, 1.0, "signal")
    assert not res.done and res.orders == []
    assert res.events[0][0] == "order_unconfirmed"


def test_max_capital_caps_the_order_size() -> None:
    ex = FakePrivateExchange(usdc=10_000.0)
    broker = _broker(ex, max_capital=100.0)
    broker.rebalance(NOW, QUOTE, 1.0, "signal")
    call = ex.created[0]
    assert call["amount"] * call["price"] <= 100.5
    assert broker.equity(QUOTE.mid) <= 100.0


def test_fees_are_converted_to_quote_currency() -> None:
    ex = FakePrivateExchange(usdc=1_000.0)
    res = _broker(ex).rebalance(NOW, QUOTE, 1.0, "signal")
    order = res.orders[0]
    assert order["fee"] == pytest.approx(order["qty"] * 0.001 * order["price"])  # frais payés en BTC


def test_bot_never_touches_holdings_it_did_not_buy() -> None:
    """Compte testnet crédité de 1 BTC + 10 000 USDC : le bot ne gère que ses 100 USDC."""
    ex = FakePrivateExchange(usdc=10_000.0, btc=1.0)
    broker = _broker(ex, max_capital=100.0)
    broker.sync()
    assert broker.units == 0 and broker.cash == 100.0
    assert broker.weight(QUOTE.mid) == 0  # le BTC préexistant n'est pas « sa » position
    res = broker.rebalance(NOW, QUOTE, 0.0, "signal")  # signal de vente : rien à vendre
    assert ex.created == [] and res.done
    broker.rebalance(NOW, QUOTE, 1.0, "signal")
    bought = ex.created[0]["amount"]
    assert bought * ex.created[0]["price"] <= 100.0
    broker.rebalance(NOW, QUOTE, 0.0, "signal")
    sold = ex.created[1]["amount"]
    assert sold <= bought  # il revend au plus ce qu'il a acheté (moins les frais pris en BTC)
    assert ex.balances["BTC"] == pytest.approx(1.0, abs=1e-6)  # le BTC d'origine est intact


def test_bot_portfolio_is_clamped_to_real_balance() -> None:
    ex = FakePrivateExchange(usdc=50.0, btc=0.0005)
    broker = _broker(ex, max_capital=100.0)
    broker.restore({"fees_paid": 0.0, "bot_units": 0.002, "bot_cash": 80.0})
    broker.sync()
    notes = broker.drain_notes()
    assert len(notes) == 2 and "vente manuelle" in notes[0]
    assert broker.units == 0.0005 and broker.cash == 50.0


def test_bot_portfolio_survives_restart() -> None:
    ex = FakePrivateExchange(usdc=10_000.0, btc=1.0)
    broker = _broker(ex, max_capital=100.0)
    broker.sync()
    broker.rebalance(NOW, QUOTE, 1.0, "signal")
    saved = broker.state()
    again = _broker(ex, max_capital=100.0)
    again.restore(saved)
    again.sync()
    assert again.units == pytest.approx(broker.units) and again.cash == pytest.approx(broker.cash)
    assert again.drain_notes() == []


# --- Trader + broker réel -----------------------------------------------------------------------


def _trader(ex: FakePrivateExchange, feed: FakeFeed, db: Path, risk: RiskConfig | None = None) -> Trader:
    strategy = Above(100.0)
    identity = make_identity("testnet", "okx", "BTC/USDC", feed.timeframe, strategy, risk)
    broker = ExchangeBroker(ex, "BTC/USDC", None, order_timeout=0.1, sleep=lambda s: None)
    return Trader(feed, Store(db), strategy, broker, risk, identity, warmup_bars=50)


def _closes() -> list[float]:
    return [99.0] * 5 + [1_000.0] * 10


def test_trader_retries_partial_fill_until_target(tmp_path: Path) -> None:
    ex = FakePrivateExchange(usdc=1_000.0, behaviors=["partial", "fill"])
    feed = FakeFeed(_closes())
    t = _trader(ex, feed, tmp_path / "t.sqlite")
    feed.advance(5)
    t.step()  # signal → ordre partiel
    assert t.pending == 1.0 and 0 < t.current_weight < 1
    t.step()  # même bougie : nouvel essai pour le reliquat
    assert t.pending is None and t.current_weight == 1.0
    orders = t.store.table("orders")
    assert orders["reason"].tolist() == ["signal", "retry"]
    assert orders["exchange_id"].notna().all() and orders["client_id"].notna().all()


def test_trader_gives_up_entry_after_max_attempts(tmp_path: Path) -> None:
    ex = FakePrivateExchange(usdc=1_000.0, behaviors=["reject"] * 10)
    feed = FakeFeed(_closes())
    t = _trader(ex, feed, tmp_path / "t.sqlite")
    feed.advance(5)
    for _ in range(5):
        t.step()
    kinds = t.store.table("events")["kind"].tolist()
    assert kinds.count("order_rejected") == 3
    assert "order_gave_up" in kinds
    assert len(ex.created) == 3  # pas de martelage infini de l'exchange


def test_trader_keeps_retrying_an_exit(tmp_path: Path) -> None:
    ex = FakePrivateExchange(usdc=0.0, btc=1.0, behaviors=["reject"] * 5 + ["fill"])
    feed = FakeFeed([99.0] * 10)
    t = _trader(ex, feed, tmp_path / "t.sqlite")
    t.current_weight = 1.0
    feed.advance(5)
    for _ in range(7):
        t.step()
    assert ex.balances["BTC"] == pytest.approx(0)  # la sortie finit par passer, sans limite d'essais


# --- Sécurité : clés, permissions, spot, mode réel -------------------------------------------------


def test_permission_report_detects_withdraw_and_ip() -> None:
    safe = check_api_permissions(FakePrivateExchange(perm="read_only,trade", ip="1.2.3.4"))
    assert safe.safe and safe.ip_restricted
    unsafe = check_api_permissions(FakePrivateExchange(perm="read_only,trade,withdraw", ip=""))
    assert unsafe.can_withdraw and not unsafe.safe and not unsafe.ip_restricted


class FakeBinance(FakePrivateExchange):
    id = "binance"

    def __init__(self, sandbox: bool, withdrawals: bool = False, ip: bool = True) -> None:
        super().__init__()
        self.isSandboxModeEnabled = sandbox
        self.withdrawals, self.ip_flag = withdrawals, ip

    def sapi_get_account_apirestrictions(self) -> dict:
        return {"enableWithdrawals": self.withdrawals, "ipRestrict": self.ip_flag, "enableSpotAndMarginTrading": True}


def test_binance_testnet_permissions_are_safe_by_construction() -> None:
    report = check_api_permissions(FakeBinance(sandbox=True))
    assert report.safe and "testnet" in report.detail


def test_binance_live_permissions_detect_withdraw() -> None:
    assert check_api_permissions(FakeBinance(sandbox=False, withdrawals=False)).safe
    unsafe = check_api_permissions(FakeBinance(sandbox=False, withdrawals=True, ip=False))
    assert unsafe.can_withdraw and not unsafe.ip_restricted


def test_binance_sandbox_points_to_spot_testnet() -> None:
    ex = create_private_exchange("binance", ApiKeys("k", "s"), sandbox=True)
    assert ex.isSandboxModeEnabled
    assert "testnet.binance.vision" in ex.urls["api"]["public"]


def test_testnet_defaults_to_binance_without_kyc() -> None:
    from bot.config import load_config

    cfg = load_config().execution
    assert (cfg.testnet_exchange, cfg.testnet_symbol) == ("binance", "BTC/USDT")


def test_non_spot_market_is_refused() -> None:
    with pytest.raises(ValueError, match="spot"):
        ensure_spot_market(FakePrivateExchange(spot=False), "BTC/USDC:USDC")


def test_api_keys_come_from_env_and_are_never_printed(tmp_path: Path, monkeypatch) -> None:
    for var in ("OKX_DEMO_API_KEY", "OKX_DEMO_API_SECRET", "OKX_DEMO_API_PASSWORD", "OKX_API_KEY", "OKX_API_SECRET"):
        monkeypatch.delenv(var, raising=False)
    env = tmp_path / ".env"
    env.write_text("OKX_DEMO_API_KEY=abcd1234wxyz\nOKX_DEMO_API_SECRET=topsecret\nOKX_DEMO_API_PASSWORD=hunter2\n")
    keys = load_api_keys("okx", sandbox=True, env_file=env)
    assert keys.api_key == "abcd1234wxyz"
    for leaked in ("topsecret", "hunter2", "wxyz"):
        assert leaked not in repr(keys)
    with pytest.raises(RuntimeError, match="OKX_API_KEY"):
        load_api_keys("okx", sandbox=False, env_file=env)  # les clés démo ne servent jamais en réel


def test_sandbox_mode_uses_okx_demo_trading() -> None:
    ex = create_private_exchange("okx", ApiKeys("k", "s", "p"), sandbox=True)
    assert ex.headers.get("x-simulated-trading") == "1"
    live = create_private_exchange("okx", ApiKeys("k", "s", "p"), sandbox=False)
    assert "x-simulated-trading" not in (live.headers or {})


SAFE = PermissionReport(can_withdraw=False, ip_restricted=True, detail="ok")


def test_live_requires_interactive_terminal() -> None:
    assert not confirm_live("okx", "BTC/USDC", 100, SAFE, 1_000, input_fn=lambda _: "x", isatty=lambda: False,
                            out=lambda _: None)


def test_live_requires_exact_phrase() -> None:
    phrase = confirmation_phrase("BTC/USDC", 100)
    assert phrase == "LIVE BTC/USDC 100"
    args = dict(isatty=lambda: True, out=lambda _: None)
    assert not confirm_live("okx", "BTC/USDC", 100, SAFE, 1_000, input_fn=lambda _: "oui", **args)
    assert not confirm_live("okx", "BTC/USDC", 100, SAFE, 1_000, input_fn=lambda _: "live btc/usdc 100", **args)
    assert confirm_live("okx", "BTC/USDC", 100, SAFE, 1_000, input_fn=lambda _: phrase, **args)


def test_live_refused_if_key_can_withdraw() -> None:
    unsafe = PermissionReport(can_withdraw=True, ip_restricted=True, detail="withdraw")
    phrase = confirmation_phrase("BTC/USDC", 100)
    assert not confirm_live("okx", "BTC/USDC", 100, unsafe, 1_000, input_fn=lambda _: phrase,
                            isatty=lambda: True, out=lambda _: None)


def test_trade_defaults_to_testnet_and_live_without_risk_is_refused() -> None:
    parser = build_parser()
    assert parser.parse_args(["trade"]).live is False  # jamais de mode réel par défaut
    args = parser.parse_args(["trade", "--live", "--no-risk"])
    with pytest.raises(SystemExit, match="garde-fous"):
        cmd_trade(args)  # refusé avant toute connexion à l'exchange
