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
class AppConfig:
    data: DataConfig = field(default_factory=DataConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)


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
    )
