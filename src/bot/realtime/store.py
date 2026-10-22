"""Journal SQLite : décisions, ordres, equity, événements, et état pour la reprise."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY, time TEXT NOT NULL, bar_time TEXT NOT NULL, close REAL, signal REAL,
    current_weight REAL, target_weight REAL, equity REAL, note TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY, time TEXT NOT NULL, side TEXT NOT NULL, qty REAL, price REAL, fee REAL,
    bid REAL, ask REAL, reason TEXT
);
CREATE TABLE IF NOT EXISTS equity (
    id INTEGER PRIMARY KEY, time TEXT NOT NULL, equity REAL, cash REAL, units REAL, price REAL
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY, time TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT
);
"""


def _iso(t: pd.Timestamp) -> str:
    return pd.Timestamp(t).isoformat()


Listener = Callable[[str, dict[str, Any]], None]


class Store:
    """`listener(record, data)` est appelé après chaque ordre (« order ») et événement (« event ») :
    c'est par là que passent les alertes. Une erreur du listener ne bloque jamais l'écriture."""

    def __init__(self, path: Path, listener: Listener | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.listener = listener
        # timeout : attend qu'un autre fil (écoute Telegram) libère le verrou au lieu d'échouer.
        self.conn = sqlite3.connect(path, timeout=30)
        # WAL : lectures possibles pendant qu'un autre fil écrit (trading + commandes Telegram en parallèle).
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Ajoute les colonnes apparues après la création d'un journal existant."""
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(orders)")}
        for name in ("exchange_id", "client_id"):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE orders ADD COLUMN {name} TEXT")

    def close(self) -> None:
        self.conn.close()

    # --- État ----------------------------------------------------------------------------

    def load_state(self) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT value FROM state WHERE key = 'trader'").fetchone()
        return None if row is None else json.loads(row[0])

    def save_state(self, state: dict[str, Any]) -> None:
        self.conn.execute(
            "INSERT INTO state (key, value) VALUES ('trader', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (json.dumps(state),),
        )

    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM state WHERE key = ?", (f"meta:{key}",)).fetchone()
        return None if row is None else json.loads(row[0])

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"meta:{key}", json.dumps(value)),
        )

    def _notify(self, record: str, data: dict[str, Any]) -> None:
        if self.listener is None:
            return
        try:
            self.listener(record, data)
        except Exception:  # noqa: BLE001 — une alerte ratée ne doit jamais casser le trading
            log.exception("Erreur dans l'envoi d'une alerte (ignorée)")

    def commit(self) -> None:
        """Valide d'un bloc tout ce qui a été écrit depuis le dernier commit (état + journal)."""
        self.conn.commit()

    # --- Journal -------------------------------------------------------------------------

    def log_decision(self, time: pd.Timestamp, bar_time: pd.Timestamp, close: float, signal: float,
                     current_weight: float, target_weight: float, equity: float, note: str = "") -> None:
        self.conn.execute(
            "INSERT INTO decisions (time, bar_time, close, signal, current_weight, target_weight, equity, note)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (_iso(time), _iso(bar_time), close, signal, current_weight, target_weight, equity, note),
        )

    def log_order(self, order: dict[str, object], bid: float, ask: float, reason: str) -> None:
        self.conn.execute(
            "INSERT INTO orders (time, side, qty, price, fee, bid, ask, reason, exchange_id, client_id)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                _iso(order["time"]), order["side"], order["qty"], order["price"], order["fee"],  # type: ignore[arg-type]
                bid, ask, reason, order.get("id"), order.get("client_id"),
            ),
        )
        self._notify("order", {**order, "reason": reason})

    def log_equity(self, time: pd.Timestamp, equity: float, cash: float, units: float, price: float) -> None:
        self.conn.execute(
            "INSERT INTO equity (time, equity, cash, units, price) VALUES (?, ?, ?, ?, ?)",
            (_iso(time), equity, cash, units, price),
        )

    def log_event(self, time: pd.Timestamp, kind: str, detail: str, commit: bool = False) -> None:
        self.conn.execute(
            "INSERT INTO events (time, kind, detail) VALUES (?, ?, ?)", (_iso(time), kind, detail)
        )
        if commit:
            self.conn.commit()
        self._notify("event", {"time": time, "kind": kind, "detail": detail})

    # --- Lecture -------------------------------------------------------------------------

    def table(self, name: str, limit: int | None = None) -> pd.DataFrame:
        if name not in ("decisions", "orders", "equity", "events"):
            raise ValueError(name)
        query = f"SELECT * FROM {name} ORDER BY id"  # noqa: S608 (nom de table validé ci-dessus)
        df = pd.read_sql_query(query, self.conn)
        if "time" in df:
            df["time"] = pd.to_datetime(df["time"], utc=True, format="ISO8601")
        return df if limit is None else df.tail(limit)
