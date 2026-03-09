"""
Labor pressure analyser — Intelligence layer.

Takes a roster (list of shifts) and forecast revenue, returns
labor cost, wage%, and a risk signal for the decision layer.
"""

from __future__ import annotations

from config.constants import SUPERANNUATION_RATE

# Thresholds match analysis/tomorrow_report.py
_AMBER_THRESHOLD = 30.0
_RED_THRESHOLD = 35.0


def analyze_labor(roster: list[dict], predicted_revenue_cents: int) -> dict:
    """
    Compute labor cost signals from tomorrow's roster.

    Args:
        roster: list of shift dicts with cost_dollars (float) key
        predicted_revenue_cents: forecast revenue in cents

    Returns dict with:
        scheduled_labor_cents, wage_pct, labor_risk ("green"/"amber"/"red"),
        staff_count, total_hours
    """
    total_labor_cents = 0
    total_hours = 0.0

    for shift in roster:
        cost = float(shift.get("cost_dollars") or 0)
        if cost > 0:
            total_labor_cents += round(cost * 100 * (1 + SUPERANNUATION_RATE))
        total_hours += float(shift.get("total_hours") or 0)

    if predicted_revenue_cents > 0:
        wage_pct = round((total_labor_cents / predicted_revenue_cents) * 100, 1)
    else:
        wage_pct = 0.0

    if wage_pct > _RED_THRESHOLD:
        risk = "red"
    elif wage_pct > _AMBER_THRESHOLD:
        risk = "amber"
    else:
        risk = "green"

    return {
        "scheduled_labor_cents": total_labor_cents,
        "wage_pct":    wage_pct,
        "labor_risk":  risk,
        "staff_count": len(roster),
        "total_hours": round(total_hours, 1),
    }
