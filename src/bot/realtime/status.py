"""Tableau de bord texte du paper trading, lu depuis le journal SQLite."""

from __future__ import annotations

import math

import pandas as pd

from bot.realtime.store import Store
from bot.realtime.trader import _migrate_state


def _pct(x: float) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x:+.2%}"


def summarize(store: Store) -> dict[str, object] | None:
    state = store.load_state()
    if state is None or state.get("started") is None:
        return None
    state = _migrate_state(state)
    equity = store.table("equity")
    orders = store.table("orders")
    events = store.table("events")
    decisions = store.table("decisions")
    started = state["started"]
    last = equity.iloc[-1] if len(equity) else None
    last_price = float(last["price"]) if last is not None else started["price"]
    last_equity = float(last["equity"]) if last is not None else started["equity"]
    curve = equity["equity"] if len(equity) else pd.Series([started["equity"]])
    peak = max(curve.max(), started["equity"])
    risk = state.get("risk") or {}
    broker = state["broker"]
    return {
        "identity": state["identity"],
        "started": started["time"],
        "equity": last_equity,
        "return": last_equity / started["equity"] - 1,
        "buy_hold_return": last_price / started["price"] - 1,
        "drawdown": last_equity / peak - 1,
        "max_drawdown": float((curve / curve.cummax()).min() - 1),
        "position_weight": min(broker["units"] * last_price / last_equity, 1.0) if last_equity > 0 else 0.0,
        "units": broker["units"],
        "cash": broker["cash"],
        "fees_paid": broker["fees_paid"],
        "stop": risk.get("stop"),
        "killed": risk.get("killed", False),
        "halted_day": risk.get("halted_day"),
        "last_bar": state["last_bar"],
        "n_decisions": len(decisions),
        "n_orders": len(orders),
        "event_counts": events["kind"].value_counts().to_dict() if len(events) else {},
        "decisions": decisions.tail(5),
        "orders": orders.tail(5),
        "events": events[~events["kind"].isin(["run_start"])].tail(8),
    }


def render(summary: dict[str, object]) -> str:
    s = summary
    ident = s["identity"]
    stop = "aucun" if s["stop"] is None else f"{s['stop']:,.2f}"
    lines = [
        f"[{ident.get('mode', 'paper').upper()}] {ident['exchange']} {ident['symbol']} {ident['timeframe']} — "
        f"{ident['strategy']}({', '.join(f'{k}={v}' for k, v in ident['params'].items())})",
        f"Démarré le {s['started'][:19]} UTC — dernière bougie traitée : {str(s['last_bar'])[:19]}",
        "",
        f"  Valeur du portefeuille : {s['equity']:,.2f}   ({_pct(s['return'])})",
        f"  Buy & hold même période : {_pct(s['buy_hold_return'])}",
        f"  Drawdown actuel / max   : {_pct(s['drawdown'])} / {_pct(s['max_drawdown'])}",
        f"  Position : {s['position_weight']:.1%} investi ({s['units']:.6f} BTC, cash {s['cash']:,.2f})",
        f"  Stop en cours : {stop} | kill switch : {'ACTIF' if s['killed'] else 'non'}"
        f" | pause journalière : {s['halted_day'] or 'non'}",
        f"  Décisions : {s['n_decisions']} | ordres : {s['n_orders']} | frais : {s['fees_paid']:,.2f}",
        f"  Événements : {s['event_counts'] or 'aucun'}",
    ]
    for title, key, cols in (
        ("Dernières décisions", "decisions", ["time", "close", "signal", "current_weight", "target_weight", "equity", "note"]),
        ("Derniers ordres", "orders", ["time", "side", "qty", "price", "fee", "reason"]),
        ("Derniers événements", "events", ["time", "kind", "detail"]),
    ):
        df: pd.DataFrame = s[key]  # type: ignore[assignment]
        lines.append(f"\n{title} :")
        if df.empty:
            lines.append("  (aucun)")
            continue
        view = df[cols].copy()
        view["time"] = view["time"].dt.strftime("%m-%d %H:%M:%S")
        if "detail" in view:
            view["detail"] = view["detail"].str.slice(0, 90)
        lines.append(view.to_string(index=False, float_format=lambda x: f"{x:,.4f}"))
    return "\n".join(lines)
