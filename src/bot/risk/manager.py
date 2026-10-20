"""RiskManager : filtre entre la position voulue par la stratégie et les ordres réellement passés."""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from bot.config import RiskConfig
from bot.risk.sizing import atr_weight, fixed_weight, stop_hit_price


@dataclass(frozen=True)
class RiskEvent:
    time: pd.Timestamp
    kind: str  # "stop", "daily_limit", "kill_switch"
    detail: str


class RiskManager:
    """État des garde-fous, mis à jour bougie par bougie.

    Cycle d'une bougie :
      on_bar_open(t, equity) → target_weight(signal, …) → [ordre] → on_entry(…) si entrée
      → check_stop(open, low) → on_bar_close(t, equity)
    Les déclenchements de clôture (limite journalière, kill switch) agissent à l'ouverture suivante.
    """

    def __init__(self, config: RiskConfig) -> None:
        self.config = config
        self.peak_equity = -math.inf
        self.day: pd.Timestamp | None = None
        self.day_start_equity = math.nan
        self.halted_day: pd.Timestamp | None = None
        self.killed = False
        self.stop: float | None = None
        self.wait_for_flat_signal = False  # après un stop : pas de rachat avant que le signal repasse à 0
        self.events: list[RiskEvent] = []

    # --- Début de bougie --------------------------------------------------------------

    def on_bar_open(self, time: pd.Timestamp, equity: float) -> None:
        day = time.normalize()
        if day != self.day:
            self.day = day
            self.day_start_equity = equity
        self.peak_equity = max(self.peak_equity, equity)

    def target_weight(self, signal: float, current_weight: float, price: float, atr: float) -> float:
        """Poids autorisé pour cette bougie, à partir du poids voulu par la stratégie."""
        if self.killed or (self.halted_day is not None and self.halted_day == self.day):
            return 0.0
        if signal <= 0:
            self.wait_for_flat_signal = False
            return 0.0
        if self.wait_for_flat_signal:
            return 0.0
        if current_weight > 0:
            return current_weight  # la taille est fixée à l'entrée, pas réajustée à chaque bougie
        c = self.config
        if c.sizing == "fixed":
            weight = fixed_weight(c.position_fraction, c.max_exposure)
        else:
            weight = atr_weight(c.risk_per_trade, self._stop_distance(price, atr) / price, c.max_exposure)
        return min(weight * signal, c.max_exposure)

    def _stop_distance(self, price: float, atr: float) -> float:
        if self.config.stop_pct is not None:
            return price * self.config.stop_pct
        if self.config.stop_atr_mult is not None:
            return self.config.stop_atr_mult * atr
        return math.nan

    def exposure_cap(self, time: pd.Timestamp, actual_weight: float) -> float | None:
        """Si la hausse du prix a fait dériver l'exposition au-delà du plafond, poids à rétablir."""
        c = self.config
        if actual_weight > c.max_exposure + c.exposure_tolerance:
            self.events.append(
                RiskEvent(time, "exposure", f"exposition {actual_weight:.0%} > plafond {c.max_exposure:.0%} : réduction")
            )
            return c.max_exposure
        return None

    def on_entry(self, fill_price: float, atr: float) -> None:
        distance = self._stop_distance(fill_price, atr)
        self.stop = fill_price - distance if math.isfinite(distance) else None

    def on_exit(self) -> None:
        self.stop = None

    # --- Pendant la bougie ------------------------------------------------------------

    def check_stop(self, time: pd.Timestamp, bar_open: float, bar_low: float) -> float | None:
        """Prix de sortie si le stop est touché sur cette bougie."""
        if self.stop is None:
            return None
        price = stop_hit_price(self.stop, bar_open, bar_low)
        if price is not None:
            gap = " (gap : ouverture sous le stop)" if price < self.stop else ""
            self.events.append(RiskEvent(time, "stop", f"stop {self.stop:,.2f} touché, sortie à {price:,.2f}{gap}"))
            self.stop = None
            self.wait_for_flat_signal = True
        return price

    # --- Fin de bougie ----------------------------------------------------------------

    def on_bar_close(self, time: pd.Timestamp, equity: float) -> None:
        c = self.config
        self.peak_equity = max(self.peak_equity, equity)
        if not self.killed and c.kill_switch_drawdown is not None:
            drawdown = equity / self.peak_equity - 1
            if drawdown <= -c.kill_switch_drawdown:
                self.killed = True
                self.events.append(
                    RiskEvent(time, "kill_switch", f"drawdown {drawdown:.1%} depuis le plus haut : arrêt définitif")
                )
        if c.daily_loss_limit is not None and self.halted_day != self.day:
            day_return = equity / self.day_start_equity - 1
            if day_return <= -c.daily_loss_limit:
                self.halted_day = self.day
                self.events.append(
                    RiskEvent(time, "daily_limit", f"perte du jour {day_return:.1%} : pause jusqu'à minuit UTC")
                )

    # --- Persistance (reprise après redémarrage) ----------------------------------------

    def to_dict(self) -> dict[str, object]:
        def ts(x: pd.Timestamp | None) -> str | None:
            return None if x is None else x.isoformat()

        return {
            "peak_equity": None if math.isinf(self.peak_equity) else self.peak_equity,
            "day": ts(self.day),
            "day_start_equity": None if math.isnan(self.day_start_equity) else self.day_start_equity,
            "halted_day": ts(self.halted_day),
            "killed": self.killed,
            "stop": self.stop,
            "wait_for_flat_signal": self.wait_for_flat_signal,
        }

    @classmethod
    def from_dict(cls, config: RiskConfig, state: dict[str, object]) -> RiskManager:
        def ts(x: object) -> pd.Timestamp | None:
            return None if x is None else pd.Timestamp(str(x))

        m = cls(config)
        m.peak_equity = -math.inf if state["peak_equity"] is None else float(state["peak_equity"])  # type: ignore[arg-type]
        m.day = ts(state["day"])
        m.day_start_equity = math.nan if state["day_start_equity"] is None else float(state["day_start_equity"])  # type: ignore[arg-type]
        m.halted_day = ts(state["halted_day"])
        m.killed = bool(state["killed"])
        m.stop = None if state["stop"] is None else float(state["stop"])  # type: ignore[arg-type]
        m.wait_for_flat_signal = bool(state["wait_for_flat_signal"])
        return m

    def reset_kill_switch(self) -> None:
        """Relance manuelle, après analyse humaine de ce qui s'est passé."""
        self.killed = False
        self.peak_equity = -math.inf
