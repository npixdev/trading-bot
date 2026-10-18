"""Chargement de la configuration YAML dans des dataclasses."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path("config/default.yaml")


@dataclass(frozen=True)
class DataConfig:
    exchange: str = "binance"
    symbol: str = "BTC/USDT"
    timeframe: str = "1h"
    since: str = "2021-01-01"
    dir: Path = Path("data")


@dataclass(frozen=True)
class TradingConfig:
    exchange: str = "okx"
    symbol: str = "BTC/USDC"


@dataclass(frozen=True)
class BacktestConfig:
    """Coûts de simulation. Frais et slippage en fraction : 0.001 = 0,1 %."""

    fee: float = 0.001  # taker OKX palier de base
    slippage: float = 0.0005  # volontairement pessimiste pour du BTC en petite taille
    initial_cash: float = 10_000.0


@dataclass(frozen=True)
class AppConfig:
    data: DataConfig = field(default_factory=DataConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    """Charge le YAML ; un fichier absent donne la config par défaut."""
    raw: dict[str, Any] = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    data = dict(raw.get("data") or {})
    if "dir" in data:
        data["dir"] = Path(data["dir"])
    if "since" in data:
        data["since"] = str(data["since"])  # YAML peut parser une date nue en datetime.date
    return AppConfig(
        data=DataConfig(**data),
        trading=TradingConfig(**(raw.get("trading") or {})),
        backtest=BacktestConfig(**(raw.get("backtest") or {})),
    )
