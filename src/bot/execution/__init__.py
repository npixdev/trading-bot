"""Exécution des ordres : simulée (PaperBroker) ou réelle via ccxt (ExchangeBroker).

C'est le SEUL endroit du code qui peut envoyer un ordre à un exchange.
"""

from bot.execution.broker import Broker, ExecutionResult, ExchangeBroker, PaperBroker
from bot.execution.exchange import (
    ApiKeys,
    PermissionReport,
    check_api_permissions,
    create_private_exchange,
    ensure_spot_market,
    load_api_keys,
)

__all__ = [
    "ApiKeys",
    "Broker",
    "ExchangeBroker",
    "ExecutionResult",
    "PaperBroker",
    "PermissionReport",
    "check_api_permissions",
    "create_private_exchange",
    "ensure_spot_market",
    "load_api_keys",
]
