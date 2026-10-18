"""Indicateurs techniques. Tous causaux : la valeur en t n'utilise que les données jusqu'à t."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """Moyenne mobile simple ; NaN tant qu'il n'y a pas `window` valeurs."""
    return series.rolling(window, min_periods=window).mean()


def bollinger(close: pd.Series, window: int, k: float) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(bande basse, moyenne, bande haute) = moyenne ∓ k écarts-types."""
    mid = sma(close, window)
    std = close.rolling(window, min_periods=window).std(ddof=0)
    return mid - k * std, mid, mid + k * std


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """RSI de Wilder (0-100). < 30 : « survendu », > 70 : « suracheté »."""
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = gain / loss
    out = 100 - 100 / (1 + rs)
    return out.where(loss != 0, 100.0).where(gain.notna())


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range de Wilder : amplitude moyenne d'une bougie, gaps compris."""
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    true_range.iloc[0] = df["high"].iloc[0] - df["low"].iloc[0]
    return true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()


def donchian(high: pd.Series, low: pd.Series, window: int) -> tuple[pd.Series, pd.Series]:
    """(plus haut, plus bas) des `window` bougies **précédentes** (la bougie courante exclue)."""
    upper = high.rolling(window, min_periods=window).max().shift(1)
    lower = low.rolling(window, min_periods=window).min().shift(1)
    return upper, lower


def hold_between(entry: pd.Series, exit: pd.Series) -> pd.Series:
    """Position 1 à partir d'un signal d'entrée, jusqu'au signal de sortie (prioritaire)."""
    state = pd.Series(np.nan, index=entry.index)
    state[entry.fillna(False).astype(bool)] = 1.0
    state[exit.fillna(False).astype(bool)] = 0.0
    return state.ffill().fillna(0.0)
