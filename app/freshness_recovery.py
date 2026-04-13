import logging
from datetime import date, timedelta
from typing import Callable

from data.storage import get_data_health

logger = logging.getLogger("autopilot.freshness")

DEFAULT_MAX_BACKFILL_DAYS = 14


def _component_map(health: dict | None) -> dict[str, dict]:
    components = (health or {}).get("components") or []
    return {
        component.get("source"): component
        for component in components
        if isinstance(component, dict) and component.get("source")
    }


def _parse_iso_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        return None


def _iter_dates(start_date: date, end_date: date) -> list[date]:
    days = (end_date - start_date).days
    return [start_date + timedelta(days=offset) for offset in range(days + 1)]


def plan_freshness_recovery(
    health: dict | None,
    *,
    today: date | None = None,
    max_backfill_days: int = DEFAULT_MAX_BACKFILL_DAYS,
) -> dict:
    today = today or date.today()
    component_by_source = _component_map(health)

    square = component_by_source.get("square_orders", {})
    deputy = component_by_source.get("deputy_rosters", {})
    profitability = component_by_source.get("daily_profitability", {})

    square_latest = _parse_iso_date(square.get("latest_date"))
    deputy_latest = _parse_iso_date(deputy.get("latest_date"))
    profitability_latest = _parse_iso_date(profitability.get("latest_date"))

    run_square = square_latest is None or square_latest < today
    run_deputy = (
        deputy_latest is None
        or deputy_latest < today
        or int(deputy.get("next_14d_shifts") or 0) <= 0
    )
    square_start: date | None = None
    deputy_start: date | None = None
    profitability_start: date | None = None
    reasons: list[str] = []

    if run_square:
        if square_latest:
            square_start = square_latest + timedelta(days=1)
            reasons.append(
                f"Square orders stale since {square_latest.isoformat()}"
            )
        else:
            square_start = today
            reasons.append("Square orders missing locally")

    if run_deputy:
        if deputy_latest and deputy_latest < today:
            deputy_start = deputy_latest + timedelta(days=1)
            reasons.append(
                f"Deputy roster data stale since {deputy_latest.isoformat()}"
            )
        else:
            deputy_start = today
            reasons.append("Deputy roster coverage missing for upcoming shifts")

    raw_profitability_start: date | None = None
    if profitability_latest is None or profitability_latest < today:
        if profitability_latest:
            raw_profitability_start = profitability_latest + timedelta(days=1)
            reasons.append(
                f"Daily profitability stale since {profitability_latest.isoformat()}"
            )
        else:
            raw_profitability_start = today
            reasons.append("Daily profitability missing locally")

    profitability_candidates = [d for d in [raw_profitability_start, square_start, deputy_start] if d]
    run_profitability = bool(profitability_candidates)
    if profitability_candidates:
        profitability_start = min(profitability_candidates)

    candidate_starts = [d for d in [square_start, deputy_start, profitability_start] if d]
    if not candidate_starts:
        return {
            "status": "fresh",
            "should_run": False,
            "run_square": False,
            "run_deputy": False,
            "run_profitability": False,
            "square_start": None,
            "deputy_start": None,
            "profitability_start": None,
            "recovery_start": None,
            "recovery_end": None,
            "recovery_days": 0,
            "clipped_to_max_days": False,
            "reasons": [],
        }

    recovery_start = min(candidate_starts)
    cap_start = today - timedelta(days=max(max_backfill_days - 1, 0))
    clipped = recovery_start < cap_start
    if clipped:
        recovery_start = cap_start

    if clipped:
        if square_start and square_start < cap_start:
            square_start = cap_start
        if deputy_start and deputy_start < cap_start:
            deputy_start = cap_start
        if profitability_start and profitability_start < cap_start:
            profitability_start = cap_start

    recovery_dates = _iter_dates(recovery_start, today)
    return {
        "status": "recovery_needed",
        "should_run": True,
        "run_square": run_square,
        "run_deputy": run_deputy,
        "run_profitability": run_profitability,
        "square_start": square_start.isoformat() if square_start else None,
        "deputy_start": deputy_start.isoformat() if deputy_start else None,
        "profitability_start": profitability_start.isoformat() if profitability_start else None,
        "recovery_start": recovery_start.isoformat(),
        "recovery_end": today.isoformat(),
        "recovery_days": len(recovery_dates),
        "clipped_to_max_days": clipped,
        "reasons": reasons,
    }


def recover_site_freshness(
    site_id: str,
    *,
    today: date | None = None,
    max_backfill_days: int = DEFAULT_MAX_BACKFILL_DAYS,
    dry_run: bool = False,
    data_health: dict | None = None,
    step_ingest_fn: Callable[[str, date, bool], dict] | None = None,
    step_deputy_fn: Callable[[str, date, bool], dict] | None = None,
    step_profitability_fn: Callable[[str, date, bool], dict] | None = None,
) -> dict:
    today = today or date.today()
    data_health = data_health or get_data_health(site_id)
    plan = plan_freshness_recovery(
        data_health,
        today=today,
        max_backfill_days=max_backfill_days,
    )

    result = {
        "status": "skipped",
        "site_id": site_id,
        "plan": plan,
        "deputy": None,
        "ingest": [],
        "profitability": [],
        "errors": [],
    }

    if not plan["should_run"]:
        result["reason"] = "already_fresh"
        return result

    if dry_run:
        result["status"] = "dry_run"
        return result

    if step_ingest_fn is None or step_deputy_fn is None or step_profitability_fn is None:
        from scripts.daily_autopilot import (
            step_deputy as default_step_deputy,
            step_ingest as default_step_ingest,
            step_profitability as default_step_profitability,
        )

        step_ingest_fn = step_ingest_fn or default_step_ingest
        step_deputy_fn = step_deputy_fn or default_step_deputy
        step_profitability_fn = step_profitability_fn or default_step_profitability

    recovery_start = date.fromisoformat(plan["recovery_start"])
    recovery_end = date.fromisoformat(plan["recovery_end"])
    square_start = _parse_iso_date(plan.get("square_start"))
    deputy_start = _parse_iso_date(plan.get("deputy_start"))
    profitability_start = _parse_iso_date(plan.get("profitability_start"))

    if plan["run_deputy"]:
        try:
            deputy_result = step_deputy_fn(site_id, deputy_start or recovery_start, False)
            result["deputy"] = deputy_result
            if isinstance(deputy_result, dict) and deputy_result.get("status") == "error":
                result["errors"].append(
                    f"deputy:{deputy_result.get('error') or 'unknown error'}"
                )
        except Exception as exc:
            logger.exception("Deputy freshness recovery failed")
            result["errors"].append(f"deputy:{exc}")

    ingest_dates = (
        _iter_dates(square_start, recovery_end)
        if plan["run_square"] and square_start
        else []
    )
    profitability_dates = (
        _iter_dates(profitability_start, recovery_end)
        if plan["run_profitability"] and profitability_start
        else []
    )
    ingest_results_by_date: dict[date, dict] = {}

    for run_date in ingest_dates:
        ingest_result = None
        try:
            ingest_result = step_ingest_fn(site_id, run_date, False)
            ingest_results_by_date[run_date] = ingest_result
            result["ingest"].append({"date": run_date.isoformat(), "result": ingest_result})
            if isinstance(ingest_result, dict) and ingest_result.get("status") == "error":
                result["errors"].append(
                    f"ingest:{run_date.isoformat()}:{ingest_result.get('error') or 'unknown error'}"
                )
        except Exception as exc:
            logger.exception("Square freshness recovery failed for %s", run_date)
            result["errors"].append(f"ingest:{run_date.isoformat()}:{exc}")
            ingest_results_by_date[run_date] = {"status": "error", "error": str(exc)}
            result["ingest"].append(
                {
                    "date": run_date.isoformat(),
                    "result": {"status": "error", "error": str(exc)},
                }
            )

    for run_date in profitability_dates:
        ingest_result = ingest_results_by_date.get(run_date)
        should_run_profitability = (
            not plan["run_square"]
            or run_date not in ingest_results_by_date
            or ingest_result.get("status") in ("ok", "no_data")
        )
        if not should_run_profitability:
            continue

        try:
            pnl_result = step_profitability_fn(site_id, run_date, False)
            result["profitability"].append({"date": run_date.isoformat(), "result": pnl_result})
            if isinstance(pnl_result, dict) and pnl_result.get("status") == "error":
                result["errors"].append(
                    f"profitability:{run_date.isoformat()}:{pnl_result.get('error') or 'unknown error'}"
                )
        except Exception as exc:
            logger.exception("Profitability freshness recovery failed for %s", run_date)
            result["errors"].append(f"profitability:{run_date.isoformat()}:{exc}")
            result["profitability"].append(
                {
                    "date": run_date.isoformat(),
                    "result": {"status": "error", "error": str(exc)},
                }
            )

    if result["errors"]:
        result["status"] = "partial"
    else:
        result["status"] = "ok"

    result["ingest_days_attempted"] = len(result["ingest"])
    result["profitability_days_attempted"] = len(result["profitability"])
    return result
