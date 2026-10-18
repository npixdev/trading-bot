"""Comparaison de stratégies hors échantillon et rapport HTML."""

from bot.report.compare import StrategyOutcome, TimeframeSection, run_comparison, verdict
from bot.report.html import render_html, write_report

__all__ = ["StrategyOutcome", "TimeframeSection", "render_html", "run_comparison", "verdict", "write_report"]
