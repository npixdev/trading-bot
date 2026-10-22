"""Stratégies de la recherche pré-enregistrée (docs/RECHERCHE.md) : H1 à H4 + témoin à exposition constante.

Toutes causales : la position à la bougie t n'utilise que les données jusqu'à t (vérifié par
`find_lookahead` dans les tests, pour chaque combinaison de paramètres).
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, ClassVar

import numpy as np
import pandas as pd

from bot.strategies.base import Strategy
from bot.strategies.indicators import sma

DAYS_PER_YEAR = 365.0  # la crypto cote tous les jours


def realized_vol(close: pd.Series, window: int) -> pd.Series:
    """Volatilité annualisée des rendements journaliers (log) sur `window` jours."""
    return np.log(close).diff().rolling(window, min_periods=window).std() * math.sqrt(DAYS_PER_YEAR)


def vol_target_weight(close: pd.Series, target: float, window: int) -> pd.Series:
    """min(1, cible / volatilité réalisée), arrondi au dixième (évite des micro-ordres quotidiens)."""
    raw = (target / realized_vol(close, window)).clip(upper=1.0)
    return (np.floor(raw * 10) / 10).fillna(0.0)


@dataclass(frozen=True)
class TrendFilter(Strategy):
    """H1 — investi à 100 % au-dessus de la moyenne mobile sur `window` jours, sinon cash."""

    name: ClassVar[str] = "trend"
    grid: ClassVar[dict[str, list[Any]]] = {"window": [50, 100, 150, 200]}
    binary: ClassVar[bool] = True
    window: int = 200

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        return (df["close"] > sma(df["close"], self.window)).astype(float)


@dataclass(frozen=True)
class VolTarget(Strategy):
    """H2 — poids = min(1, volatilité cible / volatilité réalisée)."""

    name: ClassVar[str] = "voltarget"
    grid: ClassVar[dict[str, list[Any]]] = {"target": [0.4, 0.6, 0.8], "window": [20, 60]}
    binary: ClassVar[bool] = False
    target: float = 0.6
    window: int = 30

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        return vol_target_weight(df["close"], self.target, self.window)


@dataclass(frozen=True)
class TrendVol(Strategy):
    """H3 — poids du ciblage de volatilité, seulement quand la tendance est haussière."""

    name: ClassVar[str] = "trendvol"
    grid: ClassVar[dict[str, list[Any]]] = {"trend_window": [100, 200], "target": [0.4, 0.6, 0.8]}
    binary: ClassVar[bool] = False
    trend_window: int = 200
    target: float = 0.6
    vol_window: int = 30

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        close = df["close"]
        trend = (close > sma(close, self.trend_window)).astype(float)
        return trend * vol_target_weight(close, self.target, self.vol_window)


@dataclass(frozen=True)
class FixedWeight(Strategy):
    """Témoin à exposition constante : toujours `weight` investi (ex. 0,6 = 60 % BTC, 40 % cash)."""

    name: ClassVar[str] = "fixed_weight"
    weight: float = 0.5

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        return pd.Series(float(self.weight), index=df.index)


# --- H4 : régression logistique (apprentissage automatique) --------------------------------------


def ml_features(df: pd.DataFrame) -> pd.DataFrame:
    """Variables explicatives, chacune connue à la clôture du jour (aucune donnée future)."""
    close, volume = df["close"], df["volume"]
    logc = np.log(close)
    out = {f"ret_{n}j": logc.diff(n) for n in (1, 3, 7, 14, 30)}
    out.update({f"ecart_sma{n}": logc - np.log(sma(close, n)) for n in (20, 50, 200)})
    daily = logc.diff()
    out["vol_7j"] = daily.rolling(7, min_periods=7).std()
    out["vol_30j"] = daily.rolling(30, min_periods=30).std()
    out["volume_7j_30j"] = np.log(volume.rolling(7).mean() / volume.rolling(30).mean())
    return pd.DataFrame(out, index=df.index).replace([np.inf, -np.inf], np.nan)


def fit_logistic(x: np.ndarray, y: np.ndarray, l2: float, iterations: int = 50) -> np.ndarray:
    """Régression logistique L2 par la méthode de Newton (IRLS). Renvoie les coefficients [biais, w…].

    Minimise : −Σ [y·log p + (1−y)·log(1−p)] + l2/2 · ‖w‖² (le biais n'est pas régularisé).
    """
    xb = np.column_stack([np.ones(len(x)), x])
    beta = np.zeros(xb.shape[1])
    penalty = np.full(xb.shape[1], l2)
    penalty[0] = 0.0
    for _ in range(iterations):
        p = 1.0 / (1.0 + np.exp(-np.clip(xb @ beta, -30, 30)))
        gradient = xb.T @ (p - y) + penalty * beta
        hessian = (xb * (p * (1 - p))[:, None]).T @ xb + np.diag(penalty + 1e-9)
        step = np.linalg.solve(hessian, gradient)
        beta -= step
        if np.max(np.abs(step)) < 1e-8:
            break
    return beta


def predict_proba(beta: np.ndarray, x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(beta[0] + x @ beta[1:], -30, 30)))


@dataclass(frozen=True)
class LogisticML(Strategy):
    """H4 — probabilité que demain soit haussier ; investi si elle dépasse `threshold`.

    Réentraîné tous les `retrain_every` jours. Au réentraînement à la bougie k, le modèle n'apprend que
    sur les jours i ≤ k − 1 : l'étiquette du jour i (« le jour i+1 a-t-il monté ? ») n'est connue qu'à la
    clôture de i+1 ≤ k. Les variables sont standardisées avec la moyenne/écart-type de l'entraînement.
    """

    name: ClassVar[str] = "logistic"
    grid: ClassVar[dict[str, list[Any]]] = {"threshold": [0.50, 0.52, 0.55], "l2": [0.1, 1.0]}
    binary: ClassVar[bool] = True
    threshold: float = 0.52
    l2: float = 1.0
    retrain_every: int = 30
    min_train: int = 250

    def target_position(self, df: pd.DataFrame) -> pd.Series:
        features = ml_features(df)
        x_all = features.to_numpy()
        close = df["close"].to_numpy()
        label = np.full(len(df), np.nan)
        label[:-1] = (close[1:] > close[:-1]).astype(float)  # étiquette du jour i = hausse du jour i+1
        valid = ~np.isnan(x_all).any(axis=1)
        position = np.zeros(len(df))
        for k in range(self.min_train, len(df), self.retrain_every):
            train = valid[:k] & ~np.isnan(label[:k])
            train[k - 1] = False  # marge de prudence : on n'apprend jamais sur le jour qui précède la décision
            if train.sum() < self.min_train // 2:
                continue
            x_train = x_all[:k][train]
            mean, std = x_train.mean(axis=0), x_train.std(axis=0)
            std[std == 0] = 1.0
            beta = fit_logistic((x_train - mean) / std, label[:k][train], self.l2)
            block = slice(k, min(k + self.retrain_every, len(df)))
            x_block = x_all[block]
            ok = ~np.isnan(x_block).any(axis=1)
            proba = np.zeros(len(x_block))
            proba[ok] = predict_proba(beta, (x_block[ok] - mean) / std)
            position[block] = (proba > self.threshold).astype(float)
        return pd.Series(position, index=df.index)
