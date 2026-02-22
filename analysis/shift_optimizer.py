"""
Shift optimizer v1.

Generates recommended shift start/end blocks from predicted workload by hour.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, time, timedelta

from config.database import engine
from data.storage import get_prediction, get_rosters_for_date


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
            }
            for h, rr, rs in zip(hourly, raw_required, required)
        ],
        "recommended_shifts": shifts_out,
        "summary": {
            "recommended_shift_count": len(shifts_out),
            "recommended_total_hours": round(recommended_hours, 2),
            "recommended_labor_cents": recommended_labor_cents,
            "baseline_total_hours": round(baseline_hours, 2),
            "baseline_labor_cents": baseline_labor_cents,
            "estimated_labor_delta_cents": recommended_labor_cents - baseline_labor_cents,
        },
    }


def _money_avg(values: list[int]) -> int | None:
    vals = [v for v in values if v is not None]
    if not vals:
        return None
    return round(sum(vals) / len(vals))


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
            }
        )

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
        },
    }
