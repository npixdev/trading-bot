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
class RiskConfig:
    """Garde-fous. Fractions : 0.05 = 5 %. None désactive le garde-fou correspondant."""

    sizing: str = "atr"  # "fixed" : position_fraction du capital ; "atr" : risque fixe par trade
    position_fraction: float = 1.0
    risk_per_trade: float = 0.01
    atr_period: int = 14
    stop_atr_mult: float | None = 3.0
    stop_pct: float | None = None  # alternative au stop ATR : x % sous le prix d'entrée
    max_exposure: float = 1.0
    exposure_tolerance: float = 0.05  # dérive tolérée au-dessus du plafond avant de revendre le surplus
    daily_loss_limit: float | None = 0.05
    kill_switch_drawdown: float | None = 0.30

    def __post_init__(self) -> None:
        if not 0 < self.max_exposure <= 1:
            raise ValueError("max_exposure doit être dans ]0, 1] : au-delà de 1, ce serait du levier (interdit)")
        if self.exposure_tolerance < 0:
            raise ValueError("exposure_tolerance doit être >= 0")
        if self.sizing not in ("fixed", "atr"):
            raise ValueError(f"sizing inconnu : {self.sizing!r} (attendu 'fixed' ou 'atr')")
        if not 0 < self.position_fraction <= 1:
            raise ValueError("position_fraction doit être dans ]0, 1]")
        if not 0 < self.risk_per_trade < 1:
            raise ValueError("risk_per_trade doit être dans ]0, 1[")
        if self.sizing == "atr" and self.stop_atr_mult is None and self.stop_pct is None:
            raise ValueError("le sizing 'atr' a besoin d'un stop (stop_atr_mult ou stop_pct)")
        if self.stop_atr_mult is not None and self.stop_pct is not None:
            raise ValueError("choisir stop_atr_mult OU stop_pct, pas les deux")
        for name in ("stop_pct", "daily_loss_limit", "kill_switch_drawdown"):
            value = getattr(self, name)
            if value is not None and not 0 < value < 1:
                raise ValueError(f"{name} doit être dans ]0, 1[")
        if self.stop_atr_mult is not None and self.stop_atr_mult <= 0:
            raise ValueError("stop_atr_mult doit être > 0")


@dataclass(frozen=True)
class AppConfig:
    data: DataConfig = field(default_factory=DataConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    backtest: BacktestConfig = field(default_factory=BacktestConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)


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
        risk=RiskConfig(**(raw.get("risk") or {})),
    )
