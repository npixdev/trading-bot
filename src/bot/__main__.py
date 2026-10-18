"""CLI : python -m bot <commande>."""

from __future__ import annotations

import argparse
import ast
import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from bot.backtest import run_backtest, train_test_split, walk_forward
from bot.backtest.metrics import Metrics, compute_metrics, metrics_table
from bot.config import DEFAULT_CONFIG_PATH, BacktestConfig, load_config
from bot.data import cache_path, create_exchange, find_gaps, load_cache, update_ohlcv
from bot.data.quality import find_invalid_rows
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
    results: dict[str, Metrics] = {}
    for label, (start, end) in periods.items():
        results[f"{strategy.name} {label}"] = run_backtest(df, strategy, costs, start, end).metrics
        results[f"buy&hold {label}"] = run_backtest(df, BuyAndHold(), costs, start, end).metrics
    _print_table(results)


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
    trades = pd.concat([t.trades for t in wf.tests], ignore_index=True)
    position = pd.concat([t.position for t in wf.tests])
    fees = sum(t.fees_paid for t in wf.tests)
    results = {
        "walk-forward (hors échantillon)": compute_metrics(
            wf.equity, costs.initial_cash, trades, position, fees
        ),
        "buy&hold (même période)": run_backtest(df, BuyAndHold(), costs, start, end).metrics,
    }
    print()
    _print_table(results)


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
        p.add_argument("--start", default=None, help="Début de la période (UTC)")
        p.add_argument("--end", default=None, help="Fin de la période (UTC, incluse)")
        p.add_argument("--fee", type=float, default=None, help=f"défaut : {app_cfg.backtest.fee}")
        p.add_argument("--slippage", type=float, default=None, help=f"défaut : {app_cfg.backtest.slippage}")

    bt = sub.add_parser("backtest", help="Backtest d'une stratégie, comparé au buy & hold")
    add_backtest_args(bt)
    bt.add_argument("--param", action="append", default=[], help="Paramètre, ex. avg_hold_bars=24")
    bt.add_argument("--split", type=float, default=None, help="Part train, ex. 0.7 → train/test")
    bt.set_defaults(func=cmd_backtest)

    wf = sub.add_parser("walkforward", help="Optimisation sur train, évaluation hors échantillon")
    add_backtest_args(wf)
    wf.add_argument("--grid", action="append", default=[], help="ex. seed=0..19 ou n=10,20,50")
    wf.add_argument("--train-bars", type=int, default=24 * 365)
    wf.add_argument("--test-bars", type=int, default=24 * 91)
    wf.add_argument("--metric", default="sharpe", choices=["sharpe", "sortino", "total_return", "cagr"])
    wf.add_argument("--anchored", action="store_true")
    wf.set_defaults(func=cmd_walkforward)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    args.func(args)


if __name__ == "__main__":
    main()
