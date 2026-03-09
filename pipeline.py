#!/usr/bin/env python3
"""
Clubhouse Autopilot — Pipeline Orchestrator

Clean 3-layer pipeline:
    DATA → INTELLIGENCE → DECISIONS → DELIVERY

Usage:
    .venv/bin/python pipeline.py                  # today's date, auto-detect DB
    .venv/bin/python pipeline.py --offline         # use fixture data, no DB required
    .venv/bin/python pipeline.py --date 2026-03-15
    make tomorrow                                  # calls this via tomorrow_cli.py
    make pipeline                                  # calls this directly

Output:
    reports/tomorrow_YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

# ── Layer imports ─────────────────────────────────────────────────────────────

from data.fixtures import load_demo_data
from decisions.action_engine import generate_actions
from delivery.report_generator import generate_report
from intelligence.labor_analysis import analyze_labor
from intelligence.revenue_predictor import predict_revenue
from intelligence.rush_detector import detect_rush_windows


# ── Layer 1: Data ─────────────────────────────────────────────────────────────

def ingest_data(site_id: str | None, run_date: date, offline: bool) -> dict:
    """
    Collect facts from the database or fixtures.

    Returns a data bundle with:
        site_id, site_name, historical (list), roster (list), source
    """
    if offline:
        data = load_demo_data(run_date)
        _log("data", f"offline mode — loaded fixture data ({len(data['historical'])} days history)")
        return data

    if not os.getenv("DATABASE_URL"):
        _log("data", "DATABASE_URL not set — falling back to fixtures")
        return load_demo_data(run_date)

    try:
        return _ingest_live(site_id, run_date)
    except Exception as exc:
        _log("data", f"live data unavailable ({exc}) — falling back to fixtures")
        return load_demo_data(run_date)


def _ingest_live(site_id: str | None, run_date: date) -> dict:
    from data.storage import (
        get_daily_profitability,
        get_rosters_for_date,
        get_site,
        get_site_by_location_id,
    )
    from config.settings import settings

    # Resolve site
    site = None
    if site_id:
        site = get_site(site_id)
    if not site and settings.SQUARE_LOCATION_ID:
        site = get_site_by_location_id(settings.SQUARE_LOCATION_ID)
    if not site and settings.DEFAULT_SITE_ID:
        site = get_site(settings.DEFAULT_SITE_ID)
    if not site:
        raise RuntimeError("No site found — set SQUARE_LOCATION_ID or DEFAULT_SITE_ID in .env")

    resolved_id = str(site["site_id"])
    forecast_date = run_date + timedelta(days=1)

    # Historical profitability (last 28 days)
    raw_history = get_daily_profitability(
        resolved_id,
        run_date - timedelta(days=28),
        run_date,
    )
    # Normalise to the shape intelligence layer expects
    historical = [
        {
            "date":          row.get("profit_date") or run_date,
            "revenue_cents": int(row.get("revenue_cents") or 0),
            "drink_count":   int(row.get("drink_count") or 0),
            "labor_cents":   int(row.get("labor_cost_cents") or 0),
        }
        for row in (raw_history or [])
    ]

    roster = get_rosters_for_date(resolved_id, forecast_date)
    _log("data", f"live — site={site['name']}, history={len(historical)}d, roster={len(roster)} shifts")

    return {
        "site_id":   resolved_id,
        "site_name": str(site["name"]),
        "historical": historical,
        "roster":    roster,
        "source":    "live",
    }


# ── Layer 2: Intelligence ─────────────────────────────────────────────────────

def run_intelligence(data: dict, forecast_date: date) -> dict:
    """
    Analyse collected data and return signals.

    Returns signals dict with:
        predicted_cents, predicted_drinks, confidence, label,
        rush_windows, scheduled_labor_cents, wage_pct, labor_risk,
        staff_count, total_hours
    """
    historical = data.get("historical", [])
    roster = data.get("roster", [])

    revenue = predict_revenue(historical, forecast_date)
    rush_windows = detect_rush_windows(forecast_date, revenue["predicted_drinks"])
    labor = analyze_labor(roster, revenue["predicted_cents"])

    signals = {**revenue, "rush_windows": rush_windows, **labor}
    _log(
        "intelligence",
        f"revenue=${signals['predicted_cents'] / 100:,.0f}  "
        f"drinks={signals['predicted_drinks']}  "
        f"confidence={signals['label']}  "
        f"rush_windows={len(rush_windows)}  "
        f"labor_risk={signals['labor_risk']}",
    )
    return signals


# ── Layer 3: Decisions ────────────────────────────────────────────────────────

def run_decisions(signals: dict) -> list[str]:
    """Convert signals into operator actions."""
    actions = generate_actions(signals)
    _log("decisions", f"{len(actions)} actions generated")
    return actions


# ── Layer 4: Delivery ─────────────────────────────────────────────────────────

def run_delivery(
    signals: dict,
    actions: list[str],
    forecast_date: date,
    reports_dir: Path,
    site_name: str,
) -> Path:
    """Write the markdown report and return its path."""
    path = generate_report(signals, actions, forecast_date, reports_dir, site_name)
    _log("delivery", f"report written → {path}")
    return path


# ── Orchestrator ──────────────────────────────────────────────────────────────

def run_pipeline(
    site_id: str | None = None,
    run_date: date | None = None,
    offline: bool = False,
    reports_dir: Path | None = None,
) -> Path:
    """
    Run the full pipeline and return the path to the generated report.

    This is the single public function consumed by Makefile, tests,
    and the FastAPI /pipeline/run endpoint.
    """
    run_date = run_date or date.today()
    forecast_date = run_date + timedelta(days=1)
    reports_dir = reports_dir or PROJECT_ROOT / "reports"

    data = ingest_data(site_id, run_date, offline)
    signals = run_intelligence(data, forecast_date)
    actions = run_decisions(signals)
    path = run_delivery(
        signals, actions, forecast_date, reports_dir,
        site_name=data.get("site_name", "Clubhouse"),
    )
    return path


# ── CLI entry point ───────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pipeline",
        description="Clubhouse Autopilot — generate Tomorrow Plan report.",
    )
    p.add_argument(
        "--date",
        default=str(date.today()),
        help="Business date (YYYY-MM-DD). Default: today.",
    )
    p.add_argument("--site-id", help="Site UUID (overrides env resolution).")
    p.add_argument(
        "--offline",
        action="store_true",
        help="Use fixture data; skip all DB and API calls.",
    )
    p.add_argument(
        "--reports-dir",
        default="reports",
        help="Output directory for the markdown report. Default: reports/",
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        path = run_pipeline(
            site_id=args.site_id,
            run_date=date.fromisoformat(args.date),
            offline=args.offline,
            reports_dir=PROJECT_ROOT / args.reports_dir,
        )
        print(f"Tomorrow report: {path}")
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def _log(layer: str, msg: str) -> None:
    print(f"  [{layer}] {msg}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
