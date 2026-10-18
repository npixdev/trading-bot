"""CLI : python -m bot <commande>."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

from bot.config import DEFAULT_CONFIG_PATH, load_config
from bot.data import cache_path, create_exchange, find_gaps, load_cache, update_ohlcv
from bot.data.quality import find_invalid_rows


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


def build_parser() -> argparse.ArgumentParser:
    cfg = load_config(DEFAULT_CONFIG_PATH).data
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
