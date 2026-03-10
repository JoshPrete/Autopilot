"""
Shift optimizer v1.

Generates recommended shift start/end blocks from predicted workload by hour.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta

from config.database import engine
from data.storage import (
    get_bottom_line_scorecard,
    get_prediction,
    get_rosters_for_date,
    list_operator_rules,
)


def _text(sql: str):
    from sqlalchemy import text

    return text(sql)


def _parse_hourly_from_prediction(site_id: str, target_date: date) -> list[dict]:
    pred = get_prediction(site_id, target_date)
    if not pred:
        return []
    forecast_data = pred.get("forecast_data")
    if isinstance(forecast_data, str):
        try:
            forecast_data = json.loads(forecast_data)
        except json.JSONDecodeError:
            return []
    if not isinstance(forecast_data, dict):
        return []
    hourly = forecast_data.get("hourly") or []
    if not hourly and isinstance(forecast_data.get("forecast"), dict):
        hourly = forecast_data["forecast"].get("hourly") or []
    parsed = []
    for h in hourly:
        hr = h.get("hour")
        wu = h.get("predicted_workload")
        if hr is None or wu is None:
            continue
        parsed.append({"hour": int(hr), "predicted_workload": float(wu)})
    parsed.sort(key=lambda x: x["hour"])
    return parsed


def _estimate_hourly_rate(site_id: str, target_date: date) -> float:
    with engine.connect() as conn:
        row = (
            conn.execute(
                _text(
                    """
                SELECT AVG(
                    CASE WHEN total_hours > 0
                         THEN cost_dollars / NULLIF(total_hours, 0)
                         ELSE NULL
                    END
                ) AS hourly_rate
                FROM deputy_rosters
                WHERE site_id = :sid
                  AND shift_date BETWEEN :s AND :e
                  AND COALESCE(is_open, FALSE) = FALSE
                """
                ),
                {
                    "sid": site_id,
                    "s": target_date - timedelta(days=28),
                    "e": target_date - timedelta(days=1),
                },
            )
            .mappings()
            .first()
        )
    rate = float(row["hourly_rate"]) if row and row.get("hourly_rate") is not None else 26.0
    return max(20.0, min(rate, 55.0))


def _hour_to_dt(target_date: date, hour: int) -> datetime:
    return datetime.combine(target_date, time(hour=hour))


def _smooth_staff_series(raw_required: list[int]) -> list[int]:
    if not raw_required:
        return []
    smoothed = raw_required[:]
    for i in range(1, len(smoothed)):
        if smoothed[i] - smoothed[i - 1] > 1:
            smoothed[i] = smoothed[i - 1] + 1
    for i in range(len(smoothed) - 2, -1, -1):
        if smoothed[i] - smoothed[i + 1] > 1:
            smoothed[i] = smoothed[i + 1] + 1
    return smoothed


def _get_confirmed_staffing_constraints(site_id: str, target_date: date) -> list[dict]:
    weekday = target_date.strftime("%A").lower()
    rules = list_operator_rules(
        site_id,
        statuses=["confirmed"],
        active_only=True,
        limit=100,
    )
    constraints = []
    for rule in rules:
        if rule.get("rule_type") != "staffing_constraint":
            continue
        payload = rule.get("payload") or {}
        rule_weekday = str(payload.get("day_of_week") or "").strip().lower()
        if rule_weekday and rule_weekday != weekday:
            continue
        constraints.append(rule)
    return constraints


def _constraint_indexes(hours: list[int], daypart: str) -> list[int]:
    if not hours:
        return []
    if daypart == "open":
        return [0]
    if daypart == "close":
        return [len(hours) - 1]
    return list(range(len(hours)))


def _constraint_note(payload: dict) -> str:
    daypart = payload.get("daypart") or "all_day"
    daypart_prefix = f"{daypart.capitalize()}: " if daypart != "all_day" else ""
    parts = []
    if payload.get("min_staff") is not None:
        parts.append(f"minimum {int(payload['min_staff'])} staff")
    if payload.get("requires_senior"):
        parts.append("senior coverage required")
    if payload.get("disallow_role_alone"):
        parts.append(f"do not leave {payload['disallow_role_alone']} alone")
    return daypart_prefix + (", ".join(parts) if parts else "staffing constraint")


def _apply_staffing_constraints(
    hours: list[int],
    required_staff: list[int],
    constraints: list[dict],
) -> tuple[list[int], list[dict], dict]:
    adjusted = required_staff[:]
    applied = []
    metadata = {
        "requires_senior_open": False,
        "requires_senior_close": False,
        "requires_senior_all_day": False,
    }
    if not hours or not constraints:
        return adjusted, applied, metadata

    for rule in constraints:
        payload = rule.get("payload") or {}
        daypart = str(payload.get("daypart") or "all_day").lower()
        indexes = _constraint_indexes(hours, daypart)
        if not indexes:
            continue

        min_staff = payload.get("min_staff")
        disallow_role_alone = payload.get("disallow_role_alone")
        if min_staff is not None:
            effective_min_staff = max(1, int(min_staff))
        elif disallow_role_alone:
            effective_min_staff = 2
        else:
            effective_min_staff = None

        before = adjusted[:]
        if effective_min_staff is not None:
            for idx in indexes:
                adjusted[idx] = max(adjusted[idx], effective_min_staff)

        requires_senior = bool(payload.get("requires_senior"))
        if requires_senior:
            if daypart == "open":
                metadata["requires_senior_open"] = True
            elif daypart == "close":
                metadata["requires_senior_close"] = True
            else:
                metadata["requires_senior_all_day"] = True

        changed_hours = [hours[idx] for idx in indexes if adjusted[idx] != before[idx]]
        applied.append(
            {
                "rule_id": rule.get("rule_id"),
                "daypart": daypart,
                "note": _constraint_note(payload),
                "min_staff": effective_min_staff,
                "requires_senior": requires_senior,
                "disallow_role_alone": disallow_role_alone,
                "changed_hours": changed_hours,
            }
        )

    return adjusted, applied, metadata


def _annotate_shifts_with_constraints(shifts_out: list[dict], metadata: dict) -> list[dict]:
    annotated = [dict(shift) for shift in shifts_out]
    if not annotated:
        return annotated

    def _tag(shift: dict, tag: str):
        tags = list(shift.get("constraint_tags") or [])
        if tag not in tags:
            tags.append(tag)
        shift["constraint_tags"] = tags
        shift["senior_required"] = True

    if metadata.get("requires_senior_all_day"):
        for shift in annotated:
            _tag(shift, "senior_all_day")
        return annotated

    if metadata.get("requires_senior_open"):
        earliest = min(annotated, key=lambda shift: shift["start"])
        _tag(earliest, "senior_open")

    if metadata.get("requires_senior_close"):
        latest = max(annotated, key=lambda shift: shift["end"])
        _tag(latest, "senior_close")

    return annotated


def _build_shift_blocks(
    hours: list[int],
    required_staff: list[int],
    min_shift_hours: int,
    max_shift_hours: int,
) -> list[dict]:
    """
    Greedy assignment of starts/ends to satisfy required staff each hour.
    """
    if not hours:
        return []
    shifts: list[dict] = []
    active: list[dict] = []
    idx_by_hour = {h: i for i, h in enumerate(hours)}

    for h, demand in zip(hours, required_staff):
        current = len(active)
        if current < demand:
            for _ in range(demand - current):
                shift = {"start_hour": h, "end_hour": None}
                shifts.append(shift)
                active.append(shift)
        elif current > demand:
            to_close = current - demand
            closable = [s for s in active if (h - s["start_hour"]) >= min_shift_hours]
            closable.sort(key=lambda s: s["start_hour"])
            for s in closable[:to_close]:
                s["end_hour"] = h
                active.remove(s)

        # Hard max-shift cap: close and replace at current hour.
        over_max = [s for s in active if (h - s["start_hour"]) >= max_shift_hours]
        for s in over_max:
            s["end_hour"] = h
            active.remove(s)
            replacement = {"start_hour": h, "end_hour": None}
            shifts.append(replacement)
            active.append(replacement)

    close_hour = hours[-1] + 1
    for s in active:
        min_end = s["start_hour"] + min_shift_hours
        s["end_hour"] = max(close_hour, min_end)

    # Normalize and remove zero/negative durations.
    normalized = []
    for s in shifts:
        if s["end_hour"] <= s["start_hour"]:
            continue
        if s["start_hour"] not in idx_by_hour:
            continue
        normalized.append(s)
    return normalized


def optimize_shifts(
    site_id: str,
    target_date: date,
    target_wu_per_person: float = 3.0,
    min_shift_hours: int = 3,
    max_shift_hours: int = 9,
    base_floor_staff: int = 1,
) -> dict:
    hourly = _parse_hourly_from_prediction(site_id, target_date)
    if not hourly:
        return {
            "site_id": site_id,
            "target_date": target_date.isoformat(),
            "status": "no_prediction",
            "message": "No stored prediction with hourly forecast for this date.",
            "hours": [],
            "recommended_shifts": [],
        }

    hours = [h["hour"] for h in hourly]
    raw_required = [
        max(
            base_floor_staff,
            int(math.ceil((h["predicted_workload"] or 0.0) / target_wu_per_person)),
        )
        for h in hourly
    ]
    required = _smooth_staff_series(raw_required)
    staffing_constraints = _get_confirmed_staffing_constraints(site_id, target_date)
    required, applied_constraints, constraint_meta = _apply_staffing_constraints(
        hours,
        required,
        staffing_constraints,
    )

    blocks = _build_shift_blocks(
        hours, required, min_shift_hours=min_shift_hours, max_shift_hours=max_shift_hours
    )
    avg_hourly_rate = _estimate_hourly_rate(site_id, target_date)
    recommended_hours = sum(max(0, b["end_hour"] - b["start_hour"]) for b in blocks)
    recommended_labor_cents = round(recommended_hours * avg_hourly_rate * 100)

    baseline_rosters = get_rosters_for_date(site_id, target_date)
    baseline_hours = sum(float(r.get("total_hours") or 0) for r in baseline_rosters)
    baseline_labor_cents = round(
        sum(float(r.get("cost_dollars") or 0.0) for r in baseline_rosters) * 100
    )

    shifts_out = []
    for i, b in enumerate(blocks, start=1):
        shifts_out.append(
            {
                "role_level": "L1",
                "shift_label": f"L1-{i}",
                "start": _hour_to_dt(target_date, b["start_hour"]).isoformat(),
                "end": _hour_to_dt(target_date, b["end_hour"]).isoformat(),
                "duration_hours": b["end_hour"] - b["start_hour"],
            }
        )
    shifts_out = _annotate_shifts_with_constraints(shifts_out, constraint_meta)

    return {
        "site_id": site_id,
        "target_date": target_date.isoformat(),
        "status": "ok",
        "assumptions": {
            "target_wu_per_person": target_wu_per_person,
            "min_shift_hours": min_shift_hours,
            "max_shift_hours": max_shift_hours,
            "base_floor_staff": base_floor_staff,
            "estimated_hourly_rate_dollars": round(avg_hourly_rate, 2),
        },
        "hours": [
            {
                "hour": h["hour"],
                "predicted_workload": h["predicted_workload"],
                "required_staff_raw": rr,
                "required_staff_smoothed": rs,
                "constraint_notes": [
                    constraint["note"]
                    for constraint in applied_constraints
                    if h["hour"] in constraint["changed_hours"]
                ],
            }
            for h, rr, rs in zip(hourly, raw_required, required)
        ],
        "recommended_shifts": shifts_out,
        "constraints": applied_constraints,
        "summary": {
            "recommended_shift_count": len(shifts_out),
            "recommended_total_hours": round(recommended_hours, 2),
            "recommended_labor_cents": recommended_labor_cents,
            "baseline_total_hours": round(baseline_hours, 2),
            "baseline_labor_cents": baseline_labor_cents,
            "estimated_labor_delta_cents": recommended_labor_cents - baseline_labor_cents,
            "staffing_constraints_applied_count": len(applied_constraints),
        },
    }


def _money_avg(values: list[int]) -> int | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals))


def _load_profitability_context(site_id: str) -> dict:
    try:
        scorecard = get_bottom_line_scorecard(site_id, days=30, compare_days=7, top_actions_limit=3)
    except Exception:
        return {}

    if not isinstance(scorecard, dict):
        return {}

    targets = scorecard.get("targets") or {}
    current = targets.get("current") or {}
    gaps = targets.get("gaps") or {}
    primary = targets.get("primary_lever") or {}
    financial_truth = scorecard.get("financial_truth") or {}

    return {
        "primary_lever": {
            "focus": primary.get("focus"),
            "reason": primary.get("reason"),
        },
        "gaps": {
            "weekly_labor_reduction_needed_cents": int(
                gaps.get("weekly_labor_reduction_needed_cents") or 0
            ),
            "weekly_cogs_reduction_needed_cents": int(
                gaps.get("weekly_cogs_reduction_needed_cents") or 0
            ),
            "weekly_prime_cost_reduction_needed_cents": int(
                gaps.get("weekly_prime_cost_reduction_needed_cents") or 0
            ),
            "weekly_overhead_absorption_cents": int(
                gaps.get("weekly_overhead_absorption_cents") or 0
            ),
            "weekly_revenue_needed_for_net_margin_target_cents": int(
                gaps.get("weekly_revenue_needed_for_net_margin_target_cents") or 0
            ),
        },
        "current": {
            "labor_pct": current.get("labor_pct"),
            "cogs_pct": current.get("cogs_pct"),
            "prime_cost_pct": current.get("prime_cost_pct"),
            "margin_basis_net_margin_pct": current.get("margin_basis_net_margin_pct"),
            "margin_basis_source": current.get("margin_basis_source"),
            "operating_overhead_cents": int(current.get("operating_overhead_cents") or 0),
        },
        "financial_truth": {
            "mode": financial_truth.get("mode"),
            "coverage_days": int(financial_truth.get("coverage_days") or 0),
        },
    }


def _summarize_profitability_alignment(
    labor_delta_cents: int | None,
    profitability_context: dict,
) -> dict:
    labor_delta_cents = int(labor_delta_cents or 0)
    primary = profitability_context.get("primary_lever") or {}
    gaps = profitability_context.get("gaps") or {}
    focus = str(primary.get("focus") or "").strip()
    labor_target = int(gaps.get("weekly_labor_reduction_needed_cents") or 0)
    revenue_gap = int(gaps.get("weekly_revenue_needed_for_net_margin_target_cents") or 0)
    overhead_gap = int(gaps.get("weekly_overhead_absorption_cents") or 0)

    if focus in {"labor_efficiency", "mixed_margin_repair"}:
        if labor_delta_cents < 0 and labor_target > 0:
            savings = abs(labor_delta_cents)
            progress = round(min(1.0, savings / labor_target), 3) if labor_target > 0 else None
            return {
                "focus": focus,
                "labor_target_cents": labor_target,
                "progress_ratio": progress,
                "note": (
                    f"Contributes about ${savings / 100:,.0f} toward the weekly labor reduction target "
                    f"of ${labor_target / 100:,.0f}."
                ),
            }
        if labor_delta_cents > 0:
            return {
                "focus": focus,
                "labor_target_cents": labor_target,
                "progress_ratio": 0.0,
                "note": (
                    "Adds labor despite the current labor/margin gap to protect throughput and service in higher-risk windows."
                ),
            }

    if focus == "revenue_growth":
        if labor_delta_cents > 0:
            gap_basis = revenue_gap or overhead_gap
            gap_label = "revenue gap" if revenue_gap > 0 else "overhead absorption gap"
            note = "Adds coverage to protect throughput while revenue growth is the primary profitability lever."
            if gap_basis > 0:
                note += f" Current {gap_label}: ${gap_basis / 100:,.0f}/week."
            return {
                "focus": focus,
                "labor_target_cents": 0,
                "progress_ratio": None,
                "note": note,
            }
        if labor_delta_cents < 0:
            return {
                "focus": focus,
                "labor_target_cents": 0,
                "progress_ratio": None,
                "note": "Labor trim is secondary here; the main profitability task is revenue and overhead absorption.",
            }

    if focus == "cogs_control":
        return {
            "focus": focus,
            "labor_target_cents": labor_target,
            "progress_ratio": None,
            "note": "Labor changes are secondary while COGS control remains the primary profitability lever.",
        }

    return {
        "focus": focus or None,
        "labor_target_cents": labor_target,
        "progress_ratio": None,
        "note": None,
    }


def _range_profitability_summary(daily: list[dict], profitability_context: dict) -> dict:
    primary = profitability_context.get("primary_lever") or {}
    gaps = profitability_context.get("gaps") or {}
    ok_rows = [d for d in daily if d.get("status") == "ok"]
    labor_deltas = [
        int((d.get("summary") or {}).get("estimated_labor_delta_cents") or 0)
        for d in ok_rows
        if (d.get("summary") or {}).get("estimated_labor_delta_cents") is not None
    ]
    total_labor_delta = sum(labor_deltas)
    weekly_labor_delta = round((total_labor_delta / len(ok_rows)) * 7) if ok_rows else 0
    weekly_labor_savings = max(0, -weekly_labor_delta)
    labor_target = int(gaps.get("weekly_labor_reduction_needed_cents") or 0)
    progress_ratio = (
        round(min(1.0, weekly_labor_savings / labor_target), 3)
        if labor_target > 0 and weekly_labor_savings > 0
        else (0.0 if labor_target > 0 else None)
    )

    summary_note = None
    focus = str(primary.get("focus") or "").strip()
    if focus in {"labor_efficiency", "mixed_margin_repair"} and labor_target > 0:
        summary_note = (
            f"Roster plan is targeting about ${weekly_labor_savings / 100:,.0f}/week of labor savings "
            f"against a current target of ${labor_target / 100:,.0f}/week."
        )
    elif focus == "revenue_growth":
        revenue_gap = int(gaps.get("weekly_revenue_needed_for_net_margin_target_cents") or 0)
        overhead_gap = int(gaps.get("weekly_overhead_absorption_cents") or 0)
        gap_basis = revenue_gap or overhead_gap
        if gap_basis > 0:
            summary_note = (
                f"Revenue growth is the current profit lever; the business still needs about "
                f"${gap_basis / 100:,.0f}/week of extra contribution to absorb overhead and hit margin target."
            )

    return {
        "primary_lever": primary,
        "gaps": gaps,
        "current": profitability_context.get("current") or {},
        "financial_truth": profitability_context.get("financial_truth") or {},
        "estimated_range_labor_delta_cents": total_labor_delta,
        "estimated_weekly_labor_delta_cents": weekly_labor_delta,
        "estimated_weekly_labor_savings_cents": weekly_labor_savings,
        "labor_target_progress_ratio": progress_ratio,
        "summary_note": summary_note,
    }


def optimize_shifts_range(
    site_id: str,
    start_date: date,
    days: int = 28,
    target_wu_per_person: float = 3.0,
    min_shift_hours: int = 3,
    max_shift_hours: int = 9,
    base_floor_staff: int = 1,
) -> dict:
    """
    Generate daily optimized shifts over a horizon and synthesize day-of-week templates.
    """
    daily: list[dict] = []
    end_date = start_date + timedelta(days=max(1, days) - 1)
    profitability_context = _load_profitability_context(site_id)

    for i in range(days):
        d = start_date + timedelta(days=i)
        daily_plan = optimize_shifts(
            site_id=site_id,
            target_date=d,
            target_wu_per_person=target_wu_per_person,
            min_shift_hours=min_shift_hours,
            max_shift_hours=max_shift_hours,
            base_floor_staff=base_floor_staff,
        )
        alignment = _summarize_profitability_alignment(
            (daily_plan.get("summary") or {}).get("estimated_labor_delta_cents"),
            profitability_context,
        )
        daily_plan["profitability_alignment"] = alignment
        daily.append(daily_plan)

    by_dow: dict[int, list[dict]] = {k: [] for k in range(7)}
    for p in daily:
        d = date.fromisoformat(p["target_date"])
        by_dow[d.weekday()].append(p)

    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    templates = []
    for dow in range(7):
        rows = [r for r in by_dow[dow] if r.get("status") == "ok"]
        if not rows:
            templates.append(
                {
                    "day_of_week": dow_names[dow],
                    "sample_days": 0,
                    "status": "insufficient_data",
                    "template_shifts": [],
                    "avg_recommended_total_hours": None,
                    "avg_recommended_labor_cents": None,
                    "avg_estimated_labor_delta_cents": None,
                    "profitability_alignment": None,
                }
            )
            continue

        shift_counter: dict[tuple[int, int], int] = {}
        for r in rows:
            for s in r.get("recommended_shifts", []):
                sh = datetime.fromisoformat(s["start"]).hour
                eh = datetime.fromisoformat(s["end"]).hour
                shift_counter[(sh, eh)] = shift_counter.get((sh, eh), 0) + 1

        # Keep commonly recurring blocks (>= 35% of samples).
        threshold = max(1, math.ceil(len(rows) * 0.35))
        common = sorted(
            [k for k, v in shift_counter.items() if v >= threshold],
            key=lambda x: (x[0], x[1]),
        )
        constraint_counter: dict[str, int] = {}
        requires_senior_coverage = False
        for row in rows:
            for constraint in row.get("constraints") or []:
                note = str(constraint.get("note") or "").strip()
                if note:
                    constraint_counter[note] = constraint_counter.get(note, 0) + 1
                if constraint.get("requires_senior"):
                    requires_senior_coverage = True
        template_shifts = [
            {
                "role_level": "L1",
                "start_hour": sh,
                "end_hour": eh,
                "duration_hours": eh - sh,
                "frequency": shift_counter[(sh, eh)],
            }
            for sh, eh in common
        ]

        templates.append(
            {
                "day_of_week": dow_names[dow],
                "sample_days": len(rows),
                "status": "ok",
                "template_shifts": template_shifts,
                "constraints": [
                    {"note": note, "frequency": freq}
                    for note, freq in sorted(
                        constraint_counter.items(),
                        key=lambda item: (-item[1], item[0]),
                    )
                ],
                "requires_senior_coverage": requires_senior_coverage,
                "avg_recommended_total_hours": round(
                    sum(r["summary"]["recommended_total_hours"] for r in rows) / len(rows),
                    2,
                ),
                "avg_recommended_labor_cents": _money_avg(
                    [r["summary"].get("recommended_labor_cents") for r in rows]
                ),
                "avg_estimated_labor_delta_cents": _money_avg(
                    [r["summary"].get("estimated_labor_delta_cents") for r in rows]
                ),
                "profitability_alignment": _summarize_profitability_alignment(
                    _money_avg([r["summary"].get("estimated_labor_delta_cents") for r in rows]),
                    profitability_context,
                ),
            }
        )

    profitability_summary = _range_profitability_summary(daily, profitability_context)

    return {
        "site_id": site_id,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "days": days,
        "assumptions": {
            "target_wu_per_person": target_wu_per_person,
            "min_shift_hours": min_shift_hours,
            "max_shift_hours": max_shift_hours,
            "base_floor_staff": base_floor_staff,
        },
        "daily": daily,
        "weekly_templates": templates,
        "summary": {
            "days_with_predictions": sum(1 for d in daily if d.get("status") == "ok"),
            "days_without_predictions": sum(1 for d in daily if d.get("status") != "ok"),
            "profitability_context": profitability_summary,
        },
    }
