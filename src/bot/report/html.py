"""Rapport HTML autonome (aucune dépendance externe) : courbes d'equity, drawdowns, tableaux."""

from __future__ import annotations

import html
import json
import math
from pathlib import Path
from typing import Any

import pandas as pd

from bot.backtest.metrics import Metrics
from bot.report.compare import TimeframeSection, verdict

TEMPLATE_PATH = Path(__file__).with_name("template.html")


def _daily(series: pd.Series) -> pd.Series:
    """Une valeur par jour suffit pour un graphique et garde le fichier léger."""
    return series.resample("1D").last().dropna()


def _clean(x: float) -> float | None:
    return None if x is None or (isinstance(x, float) and not math.isfinite(x)) else round(float(x), 6)


def _pct(x: float, signed: bool = True) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x:+.1%}" if signed else f"{x:.0%}"


def _num(x: float) -> str:
    return "n/a" if x is None or not math.isfinite(x) else f"{x:.2f}"


def _metric_cells(m: Metrics) -> str:
    cells = [
        _pct(m.total_return),
        _pct(m.cagr),
        _pct(m.max_drawdown),
        _num(m.sharpe),
        _num(m.sortino),
        str(m.n_trades),
        _pct(m.win_rate, signed=False),
        _pct(m.exposure, signed=False),
        f"{m.fees_paid:,.0f}".replace(",", " "),
    ]
    return "".join(f'<td class="num">{c}</td>' for c in cells)


def _chart_data(section: TimeframeSection) -> dict[str, Any]:
    curves = {"buy & hold": section.buy_hold.equity}
    curves.update({o.name: o.wf.equity for o in section.outcomes})
    daily = pd.DataFrame({k: _daily(v) for k, v in curves.items()}).ffill()
    drawdown = daily / daily.cummax() - 1
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in daily.index],
        "equity": {k: [_clean(v) for v in daily[k]] for k in daily},
        "drawdown": {k: [_clean(v) for v in drawdown[k]] for k in drawdown},
    }


def _section_html(i: int, s: TimeframeSection) -> str:
    bh = s.buy_hold.metrics
    start, end = s.buy_hold.equity.index[0], s.buy_hold.equity.index[-1]
    rows = [
        f'<tr><th scope="row"><span class="key" style="background:var(--bench)"></span>buy &amp; hold</th>'
        f'{_metric_cells(bh)}<td class="num">—</td><td>référence</td></tr>'
    ]
    details = []
    for j, o in enumerate(s.outcomes):
        pct = o.random_percentiles()
        pct_cell = "n/a" if pct is None else f"{pct[1]:.0%}"
        rows.append(
            f'<tr><th scope="row"><span class="key" style="background:var(--series-{j + 1})"></span>'
            f"{html.escape(o.name)}</th>{_metric_cells(o.metrics)}"
            f'<td class="num">{pct_cell}</td><td>{html.escape(verdict(o, bh))}</td></tr>'
        )
        win_rows = "".join(
            f"<tr><td>{r.test_start:%Y-%m-%d} → {r.test_end:%Y-%m-%d}</td>"
            f"<td>{html.escape(', '.join(f'{k}={v}' for k, v in r.params.items()))}</td>"
            f'<td class="num">{_num(r.train_sharpe)}</td><td class="num">{_num(r.test_sharpe)}</td>'
            f'<td class="num">{_pct(r.test_return)}</td></tr>'
            for r in o.wf.windows.itertuples()
        )
        train_mean = o.wf.windows["train_sharpe"].replace(-math.inf, math.nan).mean()
        test_mean = o.wf.windows["test_sharpe"].mean()
        bench_note = (
            ""
            if o.bench is None
            else f" Témoin au hasard : exposition {o.bench.exposure:.0%}, "
            f"positions de {o.bench.avg_hold_bars:.1f} bougies en moyenne, {len(o.bench.sharpes)} tirages."
        )
        details.append(
            f"<details><summary>{html.escape(o.name)} : paramètres choisis par fenêtre "
            f"(Sharpe moyen train {_num(train_mean)} → test {_num(test_mean)})</summary>"
            f"<p class=\"note\">{bench_note}</p>"
            f'<div class="table-wrap"><table><thead><tr><th>Période de test</th><th>Paramètres retenus</th>'
            f'<th class="num">Sharpe train</th><th class="num">Sharpe test</th><th class="num">Rendement test</th>'
            f"</tr></thead><tbody>{win_rows}</tbody></table></div></details>"
        )
    return f"""
<section>
  <h2>Timeframe {html.escape(s.timeframe)}</h2>
  <p class="sub">Hors échantillon du {start:%Y-%m-%d} au {end:%Y-%m-%d}.</p>
  <div class="legend" data-chart="{i}"></div>
  <figure><figcaption>Valeur du portefeuille (échelle logarithmique, départ 10 000)</figcaption>
    <div class="chart" data-kind="equity" data-section="{i}"></div></figure>
  <figure><figcaption>Drawdown : chute depuis le dernier sommet</figcaption>
    <div class="chart" data-kind="drawdown" data-section="{i}"></div></figure>
  <div class="table-wrap"><table>
    <thead><tr><th>Stratégie</th><th class="num">Rendement</th><th class="num">CAGR</th>
      <th class="num">Max DD</th><th class="num">Sharpe</th><th class="num">Sortino</th>
      <th class="num">Trades</th><th class="num">Win rate</th><th class="num">Exposition</th>
      <th class="num">Frais</th><th class="num">Bat le hasard</th><th>Verdict</th></tr></thead>
    <tbody>{"".join(rows)}</tbody>
  </table></div>
  {"".join(details)}
</section>"""


def render_html(sections: list[TimeframeSection], subtitle: str) -> str:
    data = [_chart_data(s) for s in sections]
    body = "".join(_section_html(i, s) for i, s in enumerate(sections))
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        template.replace("__SUBTITLE__", html.escape(subtitle))
        .replace("__SECTIONS__", body)
        .replace("__DATA__", json.dumps(data, separators=(",", ":")))
    )


def write_report(sections: list[TimeframeSection], path: Path, subtitle: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_html(sections, subtitle), encoding="utf-8")
    return path
