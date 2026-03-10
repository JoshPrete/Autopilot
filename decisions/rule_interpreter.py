"""
Rule interpreter — Decision layer.

Converts confirmed operator rules into date-specific action strings.
Only fires actions that are relevant to the forecast date.

Rule types handled:
    delivery_schedule   — fire on matching delivery day
    ordering_schedule   — fire on matching order cutoff day
    staffing_constraint — fire on matching day of week
    storage_rule        — fire every day (always-on check)
    recipe_definition   — not actionable in daily pipeline
"""

from __future__ import annotations

from datetime import date, datetime

_WEEKDAY_NAMES = [
    "monday", "tuesday", "wednesday", "thursday",
    "friday", "saturday", "sunday",
]


def apply_rules(confirmed_rules: list[dict], forecast_date: date) -> list[str]:
    """
    Return action strings triggered by confirmed operator rules for forecast_date.

    Args:
        confirmed_rules: list of confirmed rule dicts from storage
        forecast_date: the date being planned for

    Returns:
        list of action strings (may be empty)
    """
    forecast_dow = _WEEKDAY_NAMES[forecast_date.weekday()]
    actions: list[str] = []

    for rule in confirmed_rules:
        rule_type = rule.get("rule_type", "")
        payload = rule.get("payload") or {}

        if rule_type == "delivery_schedule":
            action = _delivery_action(payload, forecast_dow)
        elif rule_type == "ordering_schedule":
            action = _ordering_action(payload, forecast_dow)
        elif rule_type == "staffing_constraint":
            action = _staffing_action(payload, forecast_dow)
        elif rule_type == "storage_rule":
            action = _storage_action(payload)
        else:
            action = None  # recipe_definition not pipeline-actionable

        if action:
            actions.append(action)

    return actions


# ── Rule handlers ──────────────────────────────────────────────────────────────

def _delivery_action(payload: dict, forecast_dow: str) -> str | None:
    days = [str(d).lower() for d in (payload.get("days") or [])]
    if forecast_dow not in days:
        return None
    subject = payload.get("subject") or "Delivery"
    return (
        f"{subject} delivery today — "
        "confirm receiving area is clear and cool room has space"
    )


def _ordering_action(payload: dict, forecast_dow: str) -> str | None:
    cutoff_day = str(payload.get("cutoff_day") or "").lower()
    if forecast_dow != cutoff_day:
        return None
    subject = payload.get("subject") or "Order"
    cutoff_time = payload.get("cutoff_time") or ""
    delivery_day = str(payload.get("delivery_day") or "").capitalize()
    time_str = f" by {_display_time(cutoff_time)}" if cutoff_time else ""
    return f"Place {subject} order{time_str} — needed for {delivery_day} delivery"


def _staffing_action(payload: dict, forecast_dow: str) -> str | None:
    rule_dow = str(payload.get("day_of_week") or "").lower()
    if forecast_dow != rule_dow:
        return None

    daypart = str(payload.get("daypart") or "all_day")
    period = f" ({daypart})" if daypart != "all_day" else ""
    parts: list[str] = []

    min_staff = payload.get("min_staff")
    if min_staff is not None:
        parts.append(f"minimum {int(min_staff)} staff rostered{period}")
    if payload.get("requires_senior"):
        parts.append(f"senior staff required{period}")
    disallow = payload.get("disallow_role_alone")
    if disallow:
        parts.append(f"do not leave {disallow} alone{period}")

    return ("Staffing rule: " + "; ".join(parts)) if parts else None


def _storage_action(payload: dict) -> str | None:
    subject = payload.get("subject")
    location = payload.get("storage_location")
    if not subject or not location:
        return None
    condition = payload.get("condition")
    cond_str = f" until {condition}" if condition else ""
    return f"Check {subject} storage — should be in {location}{cond_str}"


# ── Helper ─────────────────────────────────────────────────────────────────────

def _display_time(hhmm: str) -> str:
    try:
        return datetime.strptime(hhmm, "%H:%M").strftime("%I:%M%p").lstrip("0").lower()
    except Exception:
        return hhmm
