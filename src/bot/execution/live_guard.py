"""Double verrou du mode réel : flag --live ET confirmation tapée dans un terminal interactif."""

from __future__ import annotations

import sys
from collections.abc import Callable

from bot.execution.exchange import PermissionReport


def confirmation_phrase(symbol: str, max_capital: float) -> str:
    return f"LIVE {symbol} {max_capital:g}"


def confirm_live(
    exchange: str,
    symbol: str,
    max_capital: float,
    report: PermissionReport,
    account_value: float,
    input_fn: Callable[[str], str] = input,
    isatty: Callable[[], bool] = sys.stdin.isatty,
    out: Callable[[str], None] = print,
) -> bool:
    """Renvoie True seulement si l'utilisateur tape exactement la phrase de confirmation."""
    if not isatty():
        out("Mode réel refusé : la confirmation doit être tapée dans un terminal interactif.")
        return False
    if report.can_withdraw is not False:
        out(f"Mode réel refusé : impossible de garantir que la clé n'a pas le droit de retrait ({report.detail}).")
        return False
    phrase = confirmation_phrase(symbol, max_capital)
    out("")
    out("=" * 70)
    out("  ATTENTION : MODE RÉEL — DE VRAIS ORDRES AVEC DE L'ARGENT RÉEL")
    out("=" * 70)
    out(f"  Exchange : {exchange}   Marché : {symbol} (spot, mode cash, sans levier)")
    out(f"  Capital maximum géré par le bot : {max_capital:g} (valeur du compte : {account_value:,.2f})")
    out(f"  Clé API : {report.detail}")
    if not report.ip_restricted:
        out("  ⚠ La clé n'est PAS restreinte par IP : si elle fuit, n'importe qui peut trader avec.")
    out("  Les backtests de l'étape 3 n'ont montré AUCUN avantage pour ces stratégies.")
    out("  N'engage que de l'argent que tu peux perdre en totalité.")
    out("=" * 70)
    answer = input_fn(f"Pour confirmer, tape exactement « {phrase} » : ")
    if answer.strip() != phrase:
        out("Confirmation incorrecte : arrêt, aucun ordre envoyé.")
        return False
    return True
