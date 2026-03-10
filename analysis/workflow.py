"""
Workflow intelligence: bottleneck detection and staffing ramifications.

Builds on daily efficiency intervals and Clubhouse role profiles to surface:
- likely bottleneck type by interval
- risk state under observed staffing
- counterfactual outcomes for 1/2/3/4 staffing levels
"""

from __future__ import annotations

from datetime import date

from config.constants import (
    LABOR_COST_PER_PERSON_PER_INTERVAL_CENTS,
    STAFFING_WU_PER_PERSON_TARGET,
)
from config.workflow_profiles import (
    PRIORITIES,
    WORKFLOW_ROLES,
    WORKLOAD_PER_PERSON_THRESHOLDS,
)
from data.storage import get_daily_efficiency_snapshot
from analysis.shift_optimizer import optimize_shifts_range


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _risk_level(staff_count: int, wu_per_person: float | None) -> str:
    if wu_per_person is None:
        return "red"
    thresholds = WORKLOAD_PER_PERSON_THRESHOLDS.get(max(1, min(staff_count, 4)))
    if not thresholds:
        return "yellow"
    if wu_per_person <= thresholds["green"]:
        return "green"
    if wu_per_person <= thresholds["yellow"]:
        return "yellow"
    return "red"


def _detect_bottleneck(row: dict) -> tuple[str, str]:
    """
    Heuristic bottleneck classifier from interval workload + staffing shape.
    """
    status = row.get("status")
    staff_on = int(row.get("staff_on") or 0)
    workload_units = float(row.get("workload_units") or 0.0)
    orders_count = int(row.get("orders_count") or 0)
    wu_per_person = row.get("workload_per_staff")
    rev_cents = int(row.get("revenue_cents") or 0)

    if status == "no_staff" and workload_units > 0:
        return "coverage_gap", "high"

    if staff_on <= 1 and workload_units >= 3.0:
        return "order_to_bar_transition", "high"
    if staff_on <= 2 and (wu_per_person or 0) >= 3.4:
        return "milk_finishing_capacity", "high"
    if staff_on >= 3 and status == "understaffed" and orders_count >= 4:
        return "shot_to_finish_handoff", "medium"
    if status == "overstaffed" and rev_cents < 1500:
        return "idle_labor", "medium"
    if orders_count >= 5 and workload_units >= 10 and staff_on <= 2:
        return "service_counter_queue", "high"
    return "none", "low"


def _counterfactual(row: dict, staff_count: int) -> dict:
    workload_units = float(row.get("workload_units") or 0.0)
    revenue_cents = int(row.get("revenue_cents") or 0)
    capacity = max(0.1, staff_count * STAFFING_WU_PER_PERSON_TARGET)
    overload_ratio = (
        0.0
        if workload_units <= 0
        else _clamp((workload_units - capacity) / workload_units, 0.0, 1.0)
    )

    queue_risk = round(overload_ratio, 2)
    # Conservative conversion from overload to lost revenue capture in-interval.
    expected_revenue_capture_pct = round(_clamp(1.0 - (overload_ratio * 0.45), 0.45, 1.0), 2)
    expected_revenue_cents = round(revenue_cents * expected_revenue_capture_pct)
    expected_revenue_delta_cents = expected_revenue_cents - revenue_cents

    expected_labor_cents = staff_count * LABOR_COST_PER_PERSON_PER_INTERVAL_CENTS
    baseline_staff = max(1, int(row.get("staff_on") or 0))
    baseline_labor_cents = baseline_staff * LABOR_COST_PER_PERSON_PER_INTERVAL_CENTS
    labor_delta_cents = expected_labor_cents - baseline_labor_cents
    net_delta_cents = expected_revenue_delta_cents - labor_delta_cents

    return {
        "staff_count": staff_count,
        "capacity_wu": round(capacity, 2),
        "queue_risk": queue_risk,
        "expected_revenue_capture_pct": expected_revenue_capture_pct,
        "expected_revenue_cents": expected_revenue_cents,
        "expected_revenue_delta_cents": expected_revenue_delta_cents,
        "expected_labor_cents": expected_labor_cents,
        "labor_delta_cents": labor_delta_cents,
        "estimated_net_delta_cents": net_delta_cents,
        "risk_level": _risk_level(
            staff_count, workload_units / staff_count if staff_count > 0 else None
        ),
    }


def analyze_workflow(site_id: str, target_date: date | None = None) -> dict:
    d = target_date or date.today()
    snap = get_daily_efficiency_snapshot(site_id, d)
    intervals = snap.get("intervals", [])

    analyzed = []
    bottleneck_counts: dict[str, int] = {}
    state_counts = {"1p": 0, "2p": 0, "3p": 0, "4p_plus": 0}

    for row in intervals:
        staff_on = int(row.get("staff_on") or 0)
        state_key = "4p_plus" if staff_on >= 4 else f"{max(staff_on, 1)}p"
        state_counts[state_key] = state_counts.get(state_key, 0) + 1

        observed_wu_per_person = row.get("workload_per_staff")
        observed_risk = _risk_level(max(1, staff_on), observed_wu_per_person)
        bottleneck_type, bottleneck_severity = _detect_bottleneck(row)
        bottleneck_counts[bottleneck_type] = bottleneck_counts.get(bottleneck_type, 0) + 1

        scenarios = [_counterfactual(row, sc) for sc in (1, 2, 3, 4)]

        analyzed.append(
            {
                "interval_start": row.get("interval_start"),
                "observed": {
                    "staff_on": staff_on,
                    "orders_count": int(row.get("orders_count") or 0),
                    "revenue_cents": int(row.get("revenue_cents") or 0),
                    "workload_units": float(row.get("workload_units") or 0.0),
                    "workload_per_staff": observed_wu_per_person,
                    "risk_level": observed_risk,
                    "status": row.get("status"),
                },
                "bottleneck": {
                    "type": bottleneck_type,
                    "severity": bottleneck_severity,
                },
                "scenarios": scenarios,
            }
        )

    high_impact = sorted(
        analyzed,
        key=lambda r: (
            1 if r["bottleneck"]["severity"] == "high" else 0,
            r["observed"]["revenue_cents"],
            r["observed"]["workload_units"],
        ),
        reverse=True,
    )[:12]

    return {
        "site_id": site_id,
        "date": d.isoformat(),
        "priorities": PRIORITIES,
        "workflow_roles": WORKFLOW_ROLES,
        "summary": {
            "intervals_analyzed": len(analyzed),
            "state_distribution": state_counts,
            "bottleneck_counts": bottleneck_counts,
        },
        "high_impact_intervals": high_impact,
        "intervals": analyzed,
    }


def _workflow_mode_from_template(template: dict) -> int:
    """Infer staffing mode from recurring template shift count."""
    count = len(template.get("template_shifts") or [])
    return max(1, min(4, count if count > 0 else 1))


def generate_roster_change_plan(
    site_id: str,
    start_date: date | None = None,
    days: int = 14,
    target_wu_per_person: float = 3.0,
    min_shift_hours: int = 3,
    max_shift_hours: int = 9,
    base_floor_staff: int = 1,
) -> dict:
    """
    Build execution-ready staffing/role recommendations for the next roster window.
    """
    start = start_date or date.today()
    horizon = max(7, min(days, 56))
    range_plan = optimize_shifts_range(
        site_id=site_id,
        start_date=start,
        days=horizon,
        target_wu_per_person=target_wu_per_person,
        min_shift_hours=min_shift_hours,
        max_shift_hours=max_shift_hours,
        base_floor_staff=base_floor_staff,
    )
    templates = range_plan.get("weekly_templates", [])
    daily = range_plan.get("daily", [])
    profitability_context = (range_plan.get("summary") or {}).get("profitability_context") or {}

    # Map day name -> inferred workflow mode from recurring shift blocks.
    mode_by_day: dict[str, int] = {}
    for t in templates:
        if t.get("status") != "ok":
            continue
        mode_by_day[t.get("day_of_week")] = _workflow_mode_from_template(t)

    day_rows = []
    for d in daily:
        if d.get("status") != "ok":
            day_rows.append(
                {
                    "date": d.get("target_date"),
                    "status": d.get("status"),
                    "workflow_mode": None,
                    "role_assignments": [],
                    "recommended_shift_count": 0,
                    "recommended_total_hours": (d.get("summary") or {}).get(
                        "recommended_total_hours"
                    ),
                    "estimated_labor_delta_cents": (d.get("summary") or {}).get(
                        "estimated_labor_delta_cents"
                    ),
                    "notes": "No prediction available for this day.",
                }
            )
            continue

        day_name = date.fromisoformat(d["target_date"]).strftime("%a")
        mode = mode_by_day.get(day_name, max(1, min(4, len(d.get("recommended_shifts") or []))))
        roles = [dict(role) for role in WORKFLOW_ROLES.get(mode, WORKFLOW_ROLES[1])]
        summary = d.get("summary") or {}
        labor_delta = summary.get("estimated_labor_delta_cents")
        constraints = d.get("constraints") or []
        profitability_alignment = d.get("profitability_alignment") or {}
        constraint_notes = [str(c.get("note") or "").strip() for c in constraints if c.get("note")]
        if any(c.get("requires_senior") for c in constraints):
            if roles:
                roles[0]["senior_required"] = True
            if len(roles) > 1 and any((c.get("daypart") or "") == "close" for c in constraints):
                roles[-1]["senior_required"] = True
        notes = "Use standard priorities: greet/serve, deliver, shots, prep."
        if labor_delta is not None and labor_delta > 0:
            notes = "Added staffing recommended to prevent throughput bottlenecks."
        elif labor_delta is not None and labor_delta < 0:
            notes = "Labor trim recommended in lower-demand windows while preserving service flow."
        if profitability_alignment.get("note"):
            notes += " " + str(profitability_alignment.get("note"))
        if constraint_notes:
            notes += " Constraints: " + "; ".join(dict.fromkeys(constraint_notes))

        day_rows.append(
            {
                "date": d.get("target_date"),
                "status": "ok",
                "workflow_mode": f"{mode}p",
                "role_assignments": roles,
                "constraints": constraints,
                "recommended_shift_count": summary.get("recommended_shift_count"),
                "recommended_total_hours": summary.get("recommended_total_hours"),
                "estimated_labor_delta_cents": labor_delta,
                "profitability_alignment": profitability_alignment,
                "notes": notes,
            }
        )

    return {
        "site_id": site_id,
        "start_date": start.isoformat(),
        "days": horizon,
        "priorities": PRIORITIES,
        "summary": range_plan.get("summary", {}),
        "profitability_context": profitability_context,
        "days_plan": day_rows,
        "weekly_templates": templates,
    }
