"""Graphique PNG envoyé sur Telegram : valeur du bot vs buy & hold, ordres, périodes investies, drawdown.

Palette : celle du rapport HTML (validée daltonisme) ; fond clair, lisible aussi dans le thème sombre
de Telegram. Rendu sans écran (backend Agg) : fonctionne sur un serveur ou dans Docker.
"""

from __future__ import annotations

import io

import matplotlib

matplotlib.use("Agg")

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from bot.realtime.store import Store  # noqa: E402

SURFACE, INK, INK_2, MUTED, GRID = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9"
BOT, BENCH, BUY, SELL = "#2a78d6", "#898781", "#1baf7a", "#eb6834"
WASH = (42 / 255, 120 / 255, 214 / 255, 0.10)


def _style(ax: plt.Axes) -> None:
    ax.set_facecolor(SURFACE)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.grid(axis="x", visible=False)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color("#c3c2b7")
    ax.tick_params(colors=MUTED, labelsize=9, length=0)


def _shade_positions(ax: plt.Axes, times: pd.Series, invested: pd.Series) -> None:
    start = None
    for t, on in zip(times, invested):
        if on and start is None:
            start = t
        elif not on and start is not None:
            ax.axvspan(start, t, color=WASH, linewidth=0)
            start = None
    if start is not None:
        ax.axvspan(start, times.iloc[-1], color=WASH, linewidth=0)


def equity_chart(store: Store, title: str) -> bytes | None:
    """PNG en mémoire, ou None s'il n'y a pas encore assez de points."""
    state = store.load_state()
    equity = store.table("equity")
    if state is None or state.get("started") is None or len(equity) < 2:
        return None
    eq = equity.drop_duplicates("time", keep="last").reset_index(drop=True)
    times = eq["time"].dt.tz_convert(None)  # matplotlib : dates naïves (UTC)
    value = eq["equity"].astype(float)
    start_equity, start_price = float(state["started"]["equity"]), float(state["started"]["price"])
    buy_hold = start_equity * eq["price"].astype(float) / start_price
    invested = (eq["units"].astype(float) * eq["price"].astype(float) / value) > 0.001
    drawdown = value / value.cummax() - 1

    fig, (ax, ax_dd) = plt.subplots(
        2, 1, figsize=(8, 5.2), dpi=150, sharex=True, gridspec_kw={"height_ratios": [3, 1.1], "hspace": 0.12}
    )
    fig.patch.set_facecolor(SURFACE)
    for a in (ax, ax_dd):
        _style(a)

    _shade_positions(ax, times, invested)
    ax.plot(times, buy_hold, color=BENCH, linewidth=1.6, label="Buy & hold")
    ax.plot(times, value, color=BOT, linewidth=2, label="Bot")

    orders = store.table("orders")
    if len(orders):
        o_times = orders["time"].dt.tz_convert(None)
        # Chaque ordre est placé sur la courbe du bot, à l'instant le plus proche.
        idx = times.searchsorted(o_times).clip(0, len(times) - 1)
        for side, marker, color, label in (("buy", "^", BUY, "Achat"), ("sell", "v", SELL, "Vente")):
            mask = (orders["side"] == side).to_numpy()
            if mask.any():
                ax.scatter(o_times[mask], value.iloc[idx[mask]].to_numpy(), marker=marker, s=70, color=color,
                           edgecolors=SURFACE, linewidths=1.5, zorder=5, label=label)

    ax.set_title(title, loc="left", fontsize=12, color=INK, fontweight="bold", pad=24)
    # Légende au-dessus du tracé : elle ne masque jamais une courbe.
    ax.legend(loc="lower left", bbox_to_anchor=(0, 1.0), frameon=False, fontsize=9, ncol=4, labelcolor=INK_2,
              borderaxespad=0.2, handletextpad=0.4, columnspacing=1.4)
    ax.yaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(lambda v, _: f"{v:,.0f}".replace(",", " ")
                                                                 if abs(v) >= 1000 else f"{v:,.2f}"))
    last = value.iloc[-1]
    ax.annotate(f"{last:,.2f}".replace(",", " "), (times.iloc[-1], last), xytext=(6, 0),
                textcoords="offset points", va="center", fontsize=9, color=INK)

    ax_dd.fill_between(times, drawdown * 100, 0, color=SELL, alpha=0.15, linewidth=0)
    ax_dd.plot(times, drawdown * 100, color=SELL, linewidth=1.4)
    ax_dd.set_ylabel("Drawdown %", fontsize=9, color=MUTED)
    ax_dd.set_ylim(min(drawdown.min() * 100 * 1.15, -0.5), 0)  # un drawdown est toujours ≤ 0

    span = times.iloc[-1] - times.iloc[0]
    fmt = "%H:%M" if span < pd.Timedelta(days=2) else "%d/%m"
    ax_dd.xaxis.set_major_formatter(mdates.DateFormatter(fmt))
    ax_dd.xaxis.set_major_locator(mdates.AutoDateLocator(maxticks=7))
    fig.text(0.99, 0.01, "heures UTC · zones bleutées = investi", ha="right", fontsize=7.5, color=MUTED)

    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", facecolor=SURFACE, bbox_inches="tight")
    plt.close(fig)
    return buffer.getvalue()
