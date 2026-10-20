"""Heartbeat : le bot est-il vivant ? (utilisé par le HEALTHCHECK Docker)."""

from __future__ import annotations

import pandas as pd

from bot.realtime.runner import HEARTBEAT_KEY
from bot.realtime.store import Store


def check_health(store: Store, max_age_seconds: float, now: pd.Timestamp | None = None) -> tuple[bool, str]:
    """(OK ?, explication). KO si aucun battement, ou battement plus vieux que `max_age_seconds`."""
    now = now or pd.Timestamp.now(tz="UTC")
    beat = store.get_meta(HEARTBEAT_KEY)
    if beat is None:
        return False, "aucun heartbeat : le bot n'a encore jamais terminé un tour de boucle"
    age = (now - pd.Timestamp(beat)).total_seconds()
    if age > max_age_seconds:
        return False, f"dernier heartbeat il y a {age:.0f} s (> {max_age_seconds:.0f} s) : bot bloqué ou arrêté"
    return True, f"OK, dernier heartbeat il y a {age:.0f} s"
