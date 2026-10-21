"""CLI : python -m bot <commande>."""

from __future__ import annotations

import argparse
import ast
import logging
import os
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import ccxt
import pandas as pd
from dotenv import load_dotenv

from bot.backtest import run_backtest, train_test_split, walk_forward
from bot.backtest.metrics import Metrics, metrics_table
from bot.config import DEFAULT_CONFIG_PATH, BacktestConfig, RiskConfig, load_config
from bot.data import cache_path, create_exchange, find_gaps, load_cache, update_ohlcv
from bot.data.quality import find_invalid_rows
from bot.data.resample import resample_ohlcv
from bot.execution import (
    ExchangeBroker,
    PaperBroker,
    check_api_permissions,
    create_private_exchange,
    ensure_spot_market,
    load_api_keys,
)
from bot.execution.live_guard import confirm_live
from bot.monitoring import Alerts, DailySummary, TelegramNotifier, check_health, notifier_from_env
from bot.realtime import MarketFeed, Store, Trader, run_loop
from bot.realtime.status import render, summarize
from bot.realtime.trader import ConfigMismatch, make_identity
from bot.report import run_comparison, verdict, write_report
from bot.strategies import STRATEGIES, BuyAndHold


def _utc(value: str) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _describe(df: pd.DataFrame, timeframe: str) -> None:
    print(f"\n{len(df)} bougies {timeframe}, du {df.index[0]} au {df.index[-1]} (UTC)\n")
    with pd.option_context("display.width", 120, "display.float_format", "{:,.2f}".format):
        print("Premières bougies :")
        print(df.head(3))
        print("\nDernières bougies :")
        print(df.tail(3))
    gaps = find_gaps(df, timeframe)
    invalid = find_invalid_rows(df)
    missing = int(gaps["missing"].sum()) if not gaps.empty else 0
    print(f"\nTrous : {len(gaps)} interruption(s), {missing} bougie(s) manquante(s)")
    if not gaps.empty:
        print(gaps.sort_values("missing", ascending=False).head(5).to_string(index=False))
    print(f"Bougies incohérentes : {len(invalid)}")


def cmd_fetch(args: argparse.Namespace) -> None:
    exchange = create_exchange(args.exchange)
    df = update_ohlcv(
        exchange,
        args.exchange,
        args.symbol,
        args.timeframe,
        since=_utc(args.since),
        data_dir=args.data_dir,
        until=_utc(args.until) if args.until else None,
    )
    if df.empty:
        print("Aucune donnée téléchargée.")
        return
    print(f"Cache : {cache_path(args.data_dir, args.exchange, args.symbol, args.timeframe)}")
    _describe(df, args.timeframe)


def cmd_info(args: argparse.Namespace) -> None:
    path = cache_path(args.data_dir, args.exchange, args.symbol, args.timeframe)
    df = load_cache(path)
    if df is None or df.empty:
        print(f"Pas de cache : {path}. Lance d'abord `python -m bot fetch`.")
        return
    _describe(df, args.timeframe)


def _literal(value: str) -> Any:
    try:
        return ast.literal_eval(value)
    except (ValueError, SyntaxError):
        return value


def _parse_params(items: list[str]) -> dict[str, Any]:
    """['a=1', 'b=x'] → {'a': 1, 'b': 'x'}"""
    return {k: _literal(v) for k, v in (item.split("=", 1) for item in items)}


def _parse_grid(items: list[str]) -> dict[str, list[Any]]:
    """['a=1,2,3'] → {'a': [1, 2, 3]} ; 'a=0..9' → range(0, 10)."""
    grid: dict[str, list[Any]] = {}
    for item in items:
        key, raw = item.split("=", 1)
        if ".." in raw:
            lo, hi = raw.split("..")
            grid[key] = list(range(int(lo), int(hi) + 1))
        else:
            grid[key] = [_literal(v) for v in raw.split(",")]
    return grid


def _load_market(args: argparse.Namespace) -> pd.DataFrame:
    path = cache_path(args.data_dir, args.exchange, args.symbol, args.timeframe)
    df = load_cache(path)
    if df is None or df.empty:
        raise SystemExit(f"Pas de cache : {path}. Lance d'abord `python -m bot fetch`.")
    if getattr(args, "resample", None):
        df = resample_ohlcv(df, args.resample)
    return df.loc[args.start : args.end]


def _costs(args: argparse.Namespace, base: BacktestConfig) -> BacktestConfig:
    return replace(
        base,
        fee=base.fee if args.fee is None else args.fee,
        slippage=base.slippage if args.slippage is None else args.slippage,
    )


def _print_table(results: dict[str, Metrics]) -> None:
    print(metrics_table(results).to_string())


def cmd_backtest(args: argparse.Namespace) -> None:
    df = _load_market(args)
    costs = _costs(args, load_config(DEFAULT_CONFIG_PATH).backtest)
    strategy = STRATEGIES[args.strategy](**_parse_params(args.param))
    print(f"{strategy} sur {args.symbol} {args.timeframe}, {df.index[0]:%Y-%m-%d} → {df.index[-1]:%Y-%m-%d}")
    print(f"Frais {costs.fee:.2%} par ordre, slippage {costs.slippage:.2%}\n")

    periods: dict[str, tuple[pd.Timestamp | None, pd.Timestamp | None]] = {"complet": (None, None)}
    if args.split:
        train, test = train_test_split(df, args.split)
        periods = {
            f"train ({train.index[0]:%Y-%m}→{train.index[-1]:%Y-%m})": (train.index[0], train.index[-1]),
            f"test ({test.index[0]:%Y-%m}→{test.index[-1]:%Y-%m})": (test.index[0], test.index[-1]),
        }
    risk = _risk_config(args) if args.risk else None
    results: dict[str, Metrics] = {}
    risky_runs = []
    for label, (start, end) in periods.items():
        results[f"{strategy.name} {label}"] = run_backtest(df, strategy, costs, start, end).metrics
        if risk is not None:
            run = run_backtest(df, strategy, costs, start, end, risk=risk)
            results[f"{strategy.name}+risque {label}"] = run.metrics
            risky_runs.append((label, run))
        results[f"buy&hold {label}"] = run_backtest(df, BuyAndHold(), costs, start, end).metrics
    _print_table(results)
    for label, run in risky_runs:
        _print_risk_events(label, run.events)


def _risk_config(args: argparse.Namespace) -> RiskConfig:
    return replace(load_config(DEFAULT_CONFIG_PATH).risk, **_parse_params(args.risk_param))


def _print_risk_events(label: str, events: pd.DataFrame) -> None:
    counts = events["kind"].value_counts().to_dict() if len(events) else {}
    print(f"\nGarde-fous déclenchés ({label}) : {counts or 'aucun'}")
    for kind in ("kill_switch", "daily_limit", "stop"):
        subset = events[events["kind"] == kind]
        for row in subset.head(3).itertuples():
            print(f"  {row.time:%Y-%m-%d %H:%M}  {kind:<12} {row.detail}")
        if len(subset) > 3:
            print(f"  … et {len(subset) - 3} autre(s) {kind}")


def cmd_walkforward(args: argparse.Namespace) -> None:
    df = _load_market(args)
    costs = _costs(args, load_config(DEFAULT_CONFIG_PATH).backtest)
    factory = STRATEGIES[args.strategy]
    grid = _parse_grid(args.grid)
    print(f"Walk-forward {factory.name}, grille {grid}")
    print(f"Train {args.train_bars} bougies, test {args.test_bars} bougies, critère : {args.metric}\n")
    wf = walk_forward(
        df, factory, grid, costs, args.train_bars, args.test_bars, args.metric, args.anchored
    )
    with pd.option_context("display.width", 160, "display.max_colwidth", 40):
        table = wf.windows.copy()
        table["test_start"] = table["test_start"].dt.strftime("%Y-%m-%d")
        table["test_end"] = table["test_end"].dt.strftime("%Y-%m-%d")
        print(table.to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    start, end = wf.equity.index[0], wf.equity.index[-1]
    results = {
        "walk-forward (hors échantillon)": wf.metrics,
        "buy&hold (même période)": run_backtest(df, BuyAndHold(), costs, start, end).metrics,
    }
    print()
    _print_table(results)


def cmd_compare(args: argparse.Namespace) -> None:
    args.start = args.end = None
    df = _load_market(args)
    costs = _costs(args, load_config(DEFAULT_CONFIG_PATH).backtest)
    strategies = [STRATEGIES[name] for name in args.strategies.split(",")]
    timeframes = args.timeframes.split(",")
    sections = run_comparison(
        df, timeframes, strategies, costs, args.train, args.test, n_seeds=args.seeds
    )
    for s in sections:
        bh = s.buy_hold.metrics
        print(f"\n=== {s.timeframe} : hors échantillon {s.buy_hold.equity.index[0]:%Y-%m-%d} → "
              f"{s.buy_hold.equity.index[-1]:%Y-%m-%d} ===")
        _print_table({"buy&hold": bh, **{o.name: o.metrics for o in s.outcomes}})
        for o in s.outcomes:
            pct = o.random_percentiles()
            beat = "n/a" if pct is None else f"{pct[1]:.0%}"
            print(f"  {o.name:<10} bat {beat} des stratégies aléatoires → {verdict(o, bh)}")

    subtitle = (
        f"{args.exchange} {args.symbol}, walk-forward train {args.train} / test {args.test}, "
        f"frais {costs.fee:.2%} + slippage {costs.slippage:.2%} par ordre"
    )
    stamp = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d-%H%M")
    path = write_report(sections, args.reports_dir / f"compare_{stamp}.html", subtitle)
    print(f"\nRapport : {path.resolve()}")


def _paper_setup(args: argparse.Namespace):  # noqa: ANN202
    app = load_config(DEFAULT_CONFIG_PATH)
    pc = app.paper
    timeframe = args.timeframe or pc.timeframe
    strategy_name = args.strategy or pc.strategy
    params = {**(pc.params if strategy_name == pc.strategy else {}), **_parse_params(args.param)}
    strategy = STRATEGIES[strategy_name](**params)
    risk = replace(app.risk, **pc.risk_overrides) if pc.use_risk and not args.no_risk else None
    costs = replace(app.backtest, initial_cash=pc.initial_cash)
    db = args.db or pc.db
    return app, pc, timeframe, strategy, risk, costs, db


def _maybe_reset(db: Path, reset: bool) -> None:
    if reset and db.exists():
        answer = input(f"Supprimer le journal {db} ? Tape 'oui' pour confirmer : ")
        if answer.strip().lower() != "oui":
            raise SystemExit("Annulé.")
        db.unlink()


def _run_trader(mode: str, feed: MarketFeed, broker, args, pc, strategy, risk, db: Path, exchange: str,  # noqa: ANN001
                symbol: str, timeframe: str) -> None:
    notifier = notifier_from_env()
    label = f"{mode.upper()} {exchange} {symbol} {timeframe}"
    store = Store(db, listener=Alerts(notifier, label))
    try:
        identity = make_identity(mode, exchange, symbol, timeframe, strategy, risk)
        trader = Trader(feed, store, strategy, broker, risk, identity, warmup_bars=pc.warmup_bars)
    except ConfigMismatch as exc:
        store.close()
        raise SystemExit(str(exc)) from None
    alerts_on = isinstance(notifier, TelegramNotifier)
    print(f"Journal : {db.resolve()}  |  alertes Telegram : {'actives' if alerts_on else 'non configurées'}")
    print("Ctrl+C pour arrêter (l'état est sauvegardé en continu)\n")
    daily = DailySummary(store, notifier, label)
    try:
        run_loop(trader, feed, store, args.poll or pc.poll_seconds, duration=args.duration, on_step=daily.maybe_send)
    finally:
        store.close()


def cmd_test_alert(args: argparse.Namespace) -> None:
    notifier = notifier_from_env()
    if isinstance(notifier, TelegramNotifier):
        if not notifier.send("✅ Test d'alerte du bot de trading : la configuration Telegram fonctionne."):
            raise SystemExit("Échec de l'envoi : vérifie TELEGRAM_BOT_TOKEN et TELEGRAM_CHAT_ID.")
        print(f"Message de test envoyé à la conversation {notifier.chat_id} : vérifie Telegram.")
        return
    load_dotenv(Path(".env"))
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN absent de .env — voir README, section Alertes Telegram.")
    chats = TelegramNotifier(token, "").find_chat_ids()
    if not chats:
        raise SystemExit("Aucune conversation trouvée : envoie d'abord un message (ex. « bonjour ») à ton bot sur Telegram.")
    print("Conversations trouvées — copie l'identifiant voulu dans .env (TELEGRAM_CHAT_ID=...) :")
    for chat_id, name in chats:
        print(f"  {chat_id}  ({name})")


def cmd_health(args: argparse.Namespace) -> None:
    app = load_config(DEFAULT_CONFIG_PATH)
    default = {"paper": app.paper.db, "testnet": app.execution.testnet_db, "live": app.execution.live_db}
    db = args.db or default[args.mode]
    if not db.exists():
        raise SystemExit(f"KO : pas de journal {db}")
    store = Store(db)
    try:
        ok, message = check_health(store, args.max_age)
    finally:
        store.close()
    print(message)
    raise SystemExit(0 if ok else 1)


def cmd_paper(args: argparse.Namespace) -> None:
    app, pc, timeframe, strategy, risk, costs, db = _paper_setup(args)
    exchange, symbol = args.exchange or app.trading.exchange, args.symbol or app.trading.symbol
    _maybe_reset(db, args.reset)
    print(f"PAPER TRADING (aucun ordre réel) — {exchange} {symbol} {timeframe}, {strategy}")
    print(f"Garde-fous : {'désactivés' if risk is None else risk}")
    feed = MarketFeed(exchange, symbol, timeframe)
    _run_trader("paper", feed, PaperBroker(costs), args, pc, strategy, risk, db, exchange, symbol, timeframe)


def _connect(args: argparse.Namespace, live: bool):  # noqa: ANN202
    """Connexion authentifiée + contrôles de sécurité. Ne passe aucun ordre."""
    app = load_config(DEFAULT_CONFIG_PATH)
    if live:
        exchange_name, symbol = args.exchange or app.trading.exchange, args.symbol or app.trading.symbol
    else:
        exchange_name = args.exchange or app.execution.testnet_exchange
        symbol = args.symbol or app.execution.testnet_symbol
    sandbox = not live
    try:
        keys = load_api_keys(exchange_name, sandbox=sandbox)
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
    exchange = create_private_exchange(exchange_name, keys, sandbox=sandbox)
    market = ensure_spot_market(exchange, symbol)
    report = check_api_permissions(exchange)
    print(f"[{'LIVE' if live else 'TESTNET'}] {exchange_name} {symbol} — {report.detail}")
    if report.can_withdraw:
        raise SystemExit("REFUS : cette clé a le droit de RETRAIT. Recrée-la sans ce droit (docs/CLES_API.md).")
    if report.can_withdraw is None:
        if live:
            raise SystemExit("REFUS : permissions de la clé non vérifiables sur cet exchange en mode réel.")
        print("  ⚠ Permissions non vérifiables : assure-toi que la clé n'a pas le droit de retrait.")
    if report.ip_restricted is False:
        print("  ⚠ Clé non restreinte par IP (recommandé : restreindre à l'IP de la machine du bot).")
    ec = app.execution
    max_capital = args.max_capital or ec.max_capital
    broker = ExchangeBroker(exchange, symbol, max_capital, ec.max_slippage, order_timeout=ec.order_timeout)
    broker.sync()
    print(f"  Compte : {broker.account_units:.8f} {market['base']}, {broker.account_cash:,.2f} {market['quote']} disponibles")
    print(f"  Un bot neuf gérerait : {broker.cash:,.2f} {market['quote']} (plafond {max_capital:g}) ; "
          "un bot existant reprend son propre portefeuille depuis son journal, le reste du compte n'est jamais touché")
    print(f"  Minimums du marché : {market['limits']['amount']['min']} {market['base']}, "
          f"montant {market['limits']['cost']['min']} {market['quote']}")
    try:
        fee = exchange.fetch_trading_fee(symbol)
        taker = float(fee.get("taker") or 0)
        print(f"  Frais de ton compte : maker {float(fee.get('maker') or 0):.3%}, taker {taker:.3%}"
              f" (backtests : {app.backtest.fee:.3%})")
        if taker > app.backtest.fee:
            print("  ⚠ Frais réels supérieurs à l'hypothèse des backtests : les résultats réels seront moins bons.")
    except ccxt.BaseError as exc:
        print(f"  (frais non disponibles : {type(exc).__name__})")
    return app, exchange_name, symbol, report, broker, max_capital


def _explain_ccxt_error(exc: ccxt.BaseError) -> str:
    hints = {
        ccxt.AuthenticationError: "clé ou secret refusés : vérifie .env (clés testnet ≠ clés réelles, pas d'espace en trop)",
        ccxt.PermissionDenied: "permission refusée : droits de la clé ou restriction IP",
        ccxt.InvalidNonce: "horloge du PC décalée : synchronise l'heure de Windows (Paramètres → Heure → Synchroniser)",
        ccxt.NetworkError: "exchange injoignable : connexion Internet ou maintenance",
    }
    for cls, hint in hints.items():
        if isinstance(exc, cls):
            return f"{type(exc).__name__} — {hint}\n  Détail : {str(exc)[:300]}"
    return f"{type(exc).__name__} : {str(exc)[:300]}"


def cmd_check_keys(args: argparse.Namespace) -> None:
    try:
        _connect(args, live=args.live)
    except ccxt.BaseError as exc:
        raise SystemExit(f"Échec de la vérification : {_explain_ccxt_error(exc)}") from None
    print("\nVérifications terminées. Aucun ordre n'a été envoyé.")


def cmd_trade(args: argparse.Namespace) -> None:
    app, pc, timeframe, strategy, risk, _, _ = _paper_setup(args)
    live = args.live
    if live and risk is None:
        raise SystemExit("REFUS : le mode réel exige les garde-fous (--no-risk interdit avec --live).")
    exchange_name, symbol, report, broker, max_capital = _connect(args, live)[1:]
    if live and not confirm_live(exchange_name, symbol, max_capital, report, broker.account_value(
            float(broker.exchange.fetch_ticker(symbol)["last"]))):
        raise SystemExit(1)
    db = args.db or (app.execution.live_db if live else app.execution.testnet_db)
    _maybe_reset(db, args.reset)
    print(f"{'LIVE — ORDRES RÉELS' if live else 'TESTNET — ordres réels sur fonds fictifs'} — "
          f"{exchange_name} {symbol} {timeframe}, {strategy}")
    print(f"Garde-fous : {'désactivés' if risk is None else risk}")
    feed = MarketFeed(exchange_name, symbol, timeframe, sandbox=not live)
    _run_trader("live" if live else "testnet", feed, broker, args, pc, strategy, risk, db, exchange_name, symbol,
                timeframe)


def cmd_status(args: argparse.Namespace) -> None:
    app = load_config(DEFAULT_CONFIG_PATH)
    default = {"paper": app.paper.db, "testnet": app.execution.testnet_db, "live": app.execution.live_db}
    db = args.db or default[args.mode]
    if not db.exists():
        raise SystemExit(f"Pas de journal : {db}. Lance d'abord le bot dans ce mode.")
    store = Store(db)
    try:
        summary = summarize(store)
        print("Journal vide." if summary is None else render(summary))
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    app_cfg = load_config(DEFAULT_CONFIG_PATH)
    cfg = app_cfg.data
    parser = argparse.ArgumentParser(prog="bot", description="Bot de trading crypto spot")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_market_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--exchange", default=cfg.exchange)
        p.add_argument("--symbol", default=cfg.symbol)
        p.add_argument("--timeframe", default=cfg.timeframe)
        p.add_argument("--data-dir", type=Path, default=cfg.dir)

    fetch = sub.add_parser("fetch", help="Télécharge/complète l'historique OHLCV")
    add_market_args(fetch)
    fetch.add_argument("--since", default=cfg.since, help="Date de début (UTC), ex. 2021-01-01")
    fetch.add_argument("--until", default=None, help="Date de fin exclue (UTC), défaut : maintenant")
    fetch.set_defaults(func=cmd_fetch)

    info = sub.add_parser("info", help="Résume les données en cache")
    add_market_args(info)
    info.set_defaults(func=cmd_info)

    def add_backtest_args(p: argparse.ArgumentParser) -> None:
        add_market_args(p)
        p.add_argument("--strategy", choices=sorted(STRATEGIES), required=True)
        p.add_argument("--resample", default=None, help="Agrège les bougies, ex. 4h ou 1d")
        p.add_argument("--start", default=None, help="Début de la période (UTC)")
        p.add_argument("--end", default=None, help="Fin de la période (UTC, incluse)")
        p.add_argument("--fee", type=float, default=None, help=f"défaut : {app_cfg.backtest.fee}")
        p.add_argument("--slippage", type=float, default=None, help=f"défaut : {app_cfg.backtest.slippage}")

    bt = sub.add_parser("backtest", help="Backtest d'une stratégie, comparé au buy & hold")
    add_backtest_args(bt)
    bt.add_argument("--param", action="append", default=[], help="Paramètre, ex. avg_hold_bars=24")
    bt.add_argument("--split", type=float, default=None, help="Part train, ex. 0.7 → train/test")
    bt.add_argument("--risk", action="store_true", help="Ajoute un run avec les garde-fous (section risk du YAML)")
    bt.add_argument(
        "--risk-param", action="append", default=[], help="Surcharge un garde-fou, ex. stop_atr_mult=2 ou daily_loss_limit=None"
    )
    bt.set_defaults(func=cmd_backtest)

    wf = sub.add_parser("walkforward", help="Optimisation sur train, évaluation hors échantillon")
    add_backtest_args(wf)
    wf.add_argument("--grid", action="append", default=[], help="ex. seed=0..19 ou n=10,20,50")
    wf.add_argument("--train-bars", type=int, default=24 * 365)
    wf.add_argument("--test-bars", type=int, default=24 * 91)
    wf.add_argument("--metric", default="sharpe", choices=["sharpe", "sortino", "total_return", "cagr"])
    wf.add_argument("--anchored", action="store_true")
    wf.set_defaults(func=cmd_walkforward)

    cmp = sub.add_parser("compare", help="Compare des stratégies hors échantillon + rapport HTML")
    add_market_args(cmp)
    cmp.add_argument("--strategies", default="sma,donchian,bollinger")
    cmp.add_argument("--timeframes", default="1h,4h,1d", help="Agrégés depuis --timeframe")
    cmp.add_argument("--train", default="365D", help="Durée d'entraînement par fenêtre")
    cmp.add_argument("--test", default="91D", help="Durée de test par fenêtre")
    cmp.add_argument("--seeds", type=int, default=100, help="Nombre de stratégies aléatoires témoins")
    cmp.add_argument("--fee", type=float, default=None)
    cmp.add_argument("--slippage", type=float, default=None)
    cmp.add_argument("--reports-dir", type=Path, default=Path("reports"))
    cmp.set_defaults(func=cmd_compare)

    def add_realtime_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--exchange", default=None, help="défaut : trading.exchange du YAML")
        p.add_argument("--symbol", default=None, help="défaut : trading.symbol du YAML")
        p.add_argument("--timeframe", default=None, help="défaut : paper.timeframe du YAML")
        p.add_argument("--strategy", choices=sorted(STRATEGIES), default=None)
        p.add_argument("--param", action="append", default=[], help="ex. fast=5")
        p.add_argument("--poll", type=float, default=None, help="secondes entre deux vérifications")
        p.add_argument("--duration", type=float, default=None, help="arrêt automatique après N secondes")
        p.add_argument("--db", type=Path, default=None)
        p.add_argument("--no-risk", action="store_true", help="désactive les garde-fous (déconseillé, interdit en live)")
        p.add_argument("--reset", action="store_true", help="efface le journal existant (confirmation demandée)")

    paper = sub.add_parser("paper", help="Paper trading temps réel (portefeuille fictif, aucun ordre réel)")
    add_realtime_args(paper)
    paper.set_defaults(func=cmd_paper)

    trade = sub.add_parser("trade", help="Ordres réels : TESTNET par défaut ; --live = argent réel (+ confirmation)")
    add_realtime_args(trade)
    trade.add_argument("--live", action="store_true", help="ARGENT RÉEL. Exige en plus une confirmation tapée.")
    trade.add_argument("--max-capital", type=float, default=None, help="défaut : execution.max_capital du YAML")
    trade.set_defaults(func=cmd_trade)

    check = sub.add_parser("check-keys", help="Vérifie clés, permissions, soldes et frais (aucun ordre)")
    check.add_argument("--exchange", default=None)
    check.add_argument("--symbol", default=None)
    check.add_argument("--live", action="store_true", help="vérifie les clés réelles au lieu des clés démo")
    check.add_argument("--max-capital", type=float, default=None)
    check.set_defaults(func=cmd_check_keys)

    alert = sub.add_parser("test-alert", help="Envoie une alerte Telegram de test (ou aide à trouver TELEGRAM_CHAT_ID)")
    alert.set_defaults(func=cmd_test_alert)

    health = sub.add_parser("health", help="Code 0 si le bot est vivant (heartbeat récent), 1 sinon")
    health.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
    health.add_argument("--db", type=Path, default=None)
    health.add_argument("--max-age", type=float, default=600, help="âge maximum du heartbeat, en secondes")
    health.set_defaults(func=cmd_health)

    for name in ("status", "paper-status"):
        status = sub.add_parser(name, help="Tableau de bord (journal SQLite)")
        status.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
        status.add_argument("--db", type=Path, default=None)
        status.set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s UTC %(levelname)s %(message)s",
    )
    logging.Formatter.converter = time.gmtime  # même fuseau que les bougies et le journal SQLite
    args.func(args)


if __name__ == "__main__":
    main()
