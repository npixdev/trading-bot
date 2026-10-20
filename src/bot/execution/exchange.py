"""Connexion authentifiée à l'exchange et contrôles de sécurité avant de trader."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import ccxt
from dotenv import load_dotenv


@dataclass(frozen=True)
class ApiKeys:
    api_key: str
    secret: str
    password: str | None = None  # « passphrase » OKX

    def __repr__(self) -> str:  # ne jamais afficher un secret dans un log
        return f"ApiKeys(api_key={self.api_key[:4]}…, secret=***, password={'***' if self.password else None})"


def env_prefix(exchange: str, sandbox: bool) -> str:
    return f"{exchange.upper()}_{'DEMO_' if sandbox else ''}API"


def load_api_keys(exchange: str, sandbox: bool, env_file: Path = Path(".env")) -> ApiKeys:
    """Lit les clés dans .env (jamais dans le code). Clés démo et réelles sont séparées."""
    load_dotenv(env_file, override=False)
    prefix = env_prefix(exchange, sandbox)
    key, secret = os.getenv(f"{prefix}_KEY"), os.getenv(f"{prefix}_SECRET")
    if not key or not secret:
        raise RuntimeError(
            f"Clés absentes : renseigne {prefix}_KEY, {prefix}_SECRET (et {prefix}_PASSWORD pour OKX) "
            f"dans {env_file.resolve()} — voir docs/CLES_API.md"
        )
    return ApiKeys(key, secret, os.getenv(f"{prefix}_PASSWORD") or None)


def create_private_exchange(name: str, keys: ApiKeys, sandbox: bool) -> ccxt.Exchange:
    if name not in ccxt.exchanges:
        raise ValueError(f"Exchange inconnu de ccxt : {name!r}")
    options = {"enableRateLimit": True, "apiKey": keys.api_key, "secret": keys.secret}
    if keys.password:
        options["password"] = keys.password
    exchange: ccxt.Exchange = getattr(ccxt, name)(options)
    if sandbox:
        exchange.set_sandbox_mode(True)  # OKX : en-tête x-simulated-trading = fonds fictifs
    return exchange


@dataclass(frozen=True)
class PermissionReport:
    can_withdraw: bool | None  # None = impossible à vérifier pour cet exchange
    ip_restricted: bool | None
    detail: str

    @property
    def safe(self) -> bool:
        return self.can_withdraw is False


def check_api_permissions(exchange: ccxt.Exchange) -> PermissionReport:
    """Interroge l'exchange sur les droits de la clé. Implémenté pour OKX."""
    if exchange.id == "okx":
        data = exchange.private_get_account_config()["data"][0]
        perms = str(data.get("perm", ""))
        ip = str(data.get("ip", "") or "")
        return PermissionReport(
            can_withdraw="withdraw" in perms,
            ip_restricted=bool(ip),
            detail=f"permissions : {perms or '?'} ; IP autorisées : {ip or 'aucune restriction'} ; "
            f"niveau de compte : {data.get('acctLv')}",
        )
    return PermissionReport(None, None, f"vérification des permissions non implémentée pour {exchange.id}")


def ensure_spot_market(exchange: ccxt.Exchange, symbol: str) -> dict:
    """Refuse tout ce qui n'est pas du spot (futures, perpétuels, marge)."""
    exchange.load_markets()
    market = exchange.market(symbol)
    if not market.get("spot") or market.get("contract"):
        raise ValueError(f"{symbol} sur {exchange.id} n'est pas un marché spot : refusé (spot uniquement)")
    return market
