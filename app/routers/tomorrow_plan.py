import json
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.dependencies import get_validated_site
from data.prediction_utils import normalize_prediction_record
from data.storage import get_prediction, get_rosters_for_date, list_operator_rules
from decisions.action_engine import generate_actions
from delivery.tomorrow_plan import generate_tomorrow_plan, generate_tomorrow_plan_html
from intelligence.labor_analysis import analyze_labor
from intelligence.revenue_predictor import predict_revenue
from models.recommendations import generate_pre_rush_checklist

router = APIRouter(prefix="/api/sites/{site_id}/tomorrow-plan", tags=["tomorrow-plan"])


def _get_prediction_or_404(site: dict, target_date: date) -> dict:
    prediction = get_prediction(site["site_id"], target_date)
    if not prediction:
        raise HTTPException(
            status_code=404,
            detail=f"No prediction for {target_date.isoformat()}",
        )
    return prediction


def _hour_label(hour: int | None) -> str:
    if hour is None:
        return "—"
    if hour == 0:
        return "12am"
    if hour < 12:
        return f"{hour}am"
    if hour == 12:
        return "12pm"
    return f"{hour - 12}pm"


def _short_time_label(value: str | None) -> str:
    if not value:
        return "—"
    try:
        from datetime import datetime as _dt

        rendered = _dt.fromisoformat(str(value).replace("Z", "+00:00")).strftime("%I:%M%p").lower()
        return rendered.lstrip("0")
    except Exception:
        return str(value)


def _summarize_trade_shape(hourly: list[dict], rush_windows: list[dict]) -> dict:
    if not hourly:
        return {
            "busiest_window_label": None,
            "busiest_window_drinks": None,
            "peak_hour_label": None,
            "peak_hour_workload": None,
            "quiet_hour_label": None,
            "quiet_hour_workload": None,
            "rush_window_count": len(rush_windows),
            "day_shape_label": "unknown",
            "day_shape_title": "No day-shape forecast yet",
            "day_shape_note": "Hourly trade shape is not available for this forecast.",
        }

    ranked_hours = sorted(
        hourly,
        key=lambda item: float(item.get("predicted_workload") or 0),
        reverse=True,
    )
    peak_hour = ranked_hours[0]
    quiet_hour = min(hourly, key=lambda item: float(item.get("predicted_workload") or 0))

    rush_sorted = sorted(
        rush_windows,
        key=lambda item: (
            int(item.get("predicted_drinks") or 0),
            float(item.get("predicted_workload") or 0),
        ),
        reverse=True,
    )
    busiest_window = rush_sorted[0] if rush_sorted else None

    peak_hour_value = float(peak_hour.get("predicted_workload") or 0)
    quiet_hour_value = float(quiet_hour.get("predicted_workload") or 0)
    peak_hour_num = int(peak_hour.get("hour") or 0)

    day_shape_label = "steady"
    day_shape_title = "Steady day"
    day_shape_note = "Demand looks fairly even across the trading day."

    second_peak = ranked_hours[1] if len(ranked_hours) > 1 else None
    second_peak_hour = int(second_peak.get("hour") or 0) if second_peak else None
    second_peak_value = float(second_peak.get("predicted_workload") or 0) if second_peak else 0.0
    peak_to_quiet_ratio = peak_hour_value / max(quiet_hour_value, 1.0)

    if (
        second_peak
        and abs(peak_hour_num - second_peak_hour) >= 3
        and second_peak_value >= peak_hour_value * 0.75
    ):
        day_shape_label = "two_wave"
        day_shape_title = "Two-wave day"
        day_shape_note = (
            f"Expect two meaningful lifts in trade, led by {_hour_label(peak_hour_num)} "
            f"and {_hour_label(second_peak_hour)}."
        )
    elif peak_to_quiet_ratio < 1.5:
        day_shape_label = "steady"
        day_shape_title = "Steady day"
        day_shape_note = "Trade looks relatively even, without one dominant peak hour."
    elif peak_hour_num < 10:
        day_shape_label = "front_loaded"
        day_shape_title = "Front-loaded morning peak"
        day_shape_note = (
            f"The strongest push is early, with peak pressure around {_hour_label(peak_hour_num)}."
        )
    elif peak_hour_num < 13:
        day_shape_label = "midday_led"
        day_shape_title = "Midday-led trade"
        day_shape_note = (
            f"The day builds into a late-morning / lunch-style peak around {_hour_label(peak_hour_num)}."
        )
    else:
        day_shape_label = "back_loaded"
        day_shape_title = "Back-loaded trade"
        day_shape_note = (
            f"The heavier trade is expected later, peaking around {_hour_label(peak_hour_num)}."
        )

    busiest_window_label = None
    busiest_window_drinks = None
    if busiest_window:
        busiest_window_label = (
            f"{_short_time_label(busiest_window.get('start'))} - "
            f"{_short_time_label(busiest_window.get('end'))}"
        )
        busiest_window_drinks = int(busiest_window.get("predicted_drinks") or 0)

    return {
        "busiest_window_label": busiest_window_label,
        "busiest_window_drinks": busiest_window_drinks,
        "peak_hour_label": _hour_label(int(peak_hour.get("hour") or 0)),
        "peak_hour_workload": round(peak_hour_value, 1),
        "quiet_hour_label": _hour_label(int(quiet_hour.get("hour") or 0)),
        "quiet_hour_workload": round(quiet_hour_value, 1),
        "rush_window_count": len(rush_windows),
        "day_shape_label": day_shape_label,
        "day_shape_title": day_shape_title,
        "day_shape_note": day_shape_note,
    }


@router.get("/text")
def tomorrow_plan_text(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None, description="Plan date (default: tomorrow)"),
    staff_names: str = Query(default=None, description="JSON-encoded staff_names dict"),
):
    plan_date = target_date or (date.today() + timedelta(days=1))
    prediction = _get_prediction_or_404(site, plan_date)
    names = json.loads(staff_names) if staff_names else None
    plan = generate_tomorrow_plan(
        site_name=site["name"],
        site_id=site["site_id"],
        prediction=prediction,
        staff_names=names,
    )
    return {"plan": plan, "target_date": plan_date.isoformat()}


@router.get("/html")
def tomorrow_plan_html(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None, description="Plan date (default: tomorrow)"),
    staff_names: str = Query(default=None, description="JSON-encoded staff_names dict"),
):
    plan_date = target_date or (date.today() + timedelta(days=1))
    prediction = _get_prediction_or_404(site, plan_date)
    names = json.loads(staff_names) if staff_names else None
    html = generate_tomorrow_plan_html(
        site_name=site["name"],
        site_id=site["site_id"],
        prediction=prediction,
        staff_names=names,
    )
    return HTMLResponse(content=html)


@router.get("/json")
def tomorrow_plan_json(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None, description="Plan date (default: tomorrow)"),
):
    """Return the full tomorrow plan prediction as structured JSON."""
    plan_date = target_date or (date.today() + timedelta(days=1))
    row = _get_prediction_or_404(site, plan_date)
    prediction = normalize_prediction_record(row)

    forecast = prediction.get("forecast", {})
    weather = prediction.get("weather", {})
    rush_windows = prediction.get("rush_windows", [])

    # Confidence label from score
    confidence = prediction.get("confidence")
    if confidence is not None:
        if confidence >= 0.8:
            confidence_label = "high"
        elif confidence >= 0.6:
            confidence_label = "medium"
        else:
            confidence_label = "low"
    else:
        confidence_label = None

    # Build rush window response objects with checklists
    rush_response = []
    for i, rw in enumerate(rush_windows, 1):
        rush_response.append(
            {
                "window_number": i,
                "start": rw.get("start"),
                "end": rw.get("end"),
                "duration_minutes": rw.get("duration_minutes"),
                "predicted_drinks": rw.get("predicted_drinks"),
                "wally_start_time": rw.get("wally_start_time"),
                "wally_volume_litres": rw.get("wally_volume_litres"),
                "wally_split": rw.get("wally_split", {}),
                "switch_3p_time": rw.get("switch_3p_time"),
                "alert_time": rw.get("alert_time"),
                "pre_rush_checklist": generate_pre_rush_checklist(rw),
            }
        )

    # Build hourly breakdown
    hourly_raw = forecast.get("hourly", [])
    hourly_response = []
    for h in hourly_raw:
        hour = h.get("hour")
        hourly_response.append(
            {
                "hour": hour,
                "hour_label": _hour_label(hour),
                "predicted_workload": h.get("predicted_workload"),
                "is_rush": h.get("is_rush", False),
            }
        )
    peak_trade = _summarize_trade_shape(hourly_response, rush_response)
    peak_trade["vs_typical_pct"] = None
    peak_trade["vs_typical_direction"] = None

    site_id = site["site_id"]
    predicted_drinks = (
        forecast.get("total_predicted_drinks") or prediction.get("total_predicted_drinks") or 0
    )

    # ── Revenue forecast + DOW baseline ───────────────────────────────────────
    history: list = []
    revenue_signals: dict = {}
    try:
        from data.storage import get_daily_profitability
        history = get_daily_profitability(site_id, plan_date - timedelta(days=56), plan_date) or []
        normalized_history = [
            {
                "date": r.get("profit_date") or plan_date,
                "revenue_cents": int(r.get("revenue_cents") or 0),
                "drink_count": int(r.get("drink_count") or 0),
                "labor_cents": int(r.get("labor_cost_cents") or 0),
            }
            for r in history
        ]
        if normalized_history:
            revenue_signals = predict_revenue(normalized_history, plan_date)
    except Exception:
        pass

    predicted_cents = revenue_signals.get("predicted_cents") or 0

    # ── vs-typical-DOW comparison ──────────────────────────────────────────────
    vs_typical_pct: float | None = None
    vs_typical_direction: str | None = None
    try:
        dow = plan_date.weekday()
        same_dow_drinks: list[int] = []
        for r in history:
            dc = int(r.get("drink_count") or 0)
            pd = r.get("profit_date")
            if dc <= 0 or pd is None:
                continue
            if hasattr(pd, "weekday"):
                pd_dow = pd.weekday()
            else:
                from datetime import date as _date_cls
                pd_dow = _date_cls.fromisoformat(str(pd)).weekday()
            if pd_dow == dow:
                same_dow_drinks.append(dc)
        if len(same_dow_drinks) >= 3 and predicted_drinks > 0:
            same_dow_drinks.sort()
            baseline = same_dow_drinks[len(same_dow_drinks) // 2]
            if baseline > 0:
                pct = ((predicted_drinks - baseline) / baseline) * 100
                vs_typical_pct = round(pct, 1)
                if pct >= 10:
                    vs_typical_direction = "busier"
                elif pct <= -10:
                    vs_typical_direction = "quieter"
                else:
                    vs_typical_direction = "similar"
    except Exception:
        pass

    # ── Labor analysis ────────────────────────────────────────────────────────
    labor: dict = {"scheduled_labor_cents": 0, "wage_pct": 0.0, "labor_risk": "green",
                   "staff_count": 0, "total_hours": 0.0}
    try:
        roster = get_rosters_for_date(site_id, plan_date)
        labor = analyze_labor(roster, predicted_cents)
    except Exception:
        pass

    # ── Actions (decisions layer with confirmed rules) ─────────────────────────
    actions: list[str] = []
    try:
        confirmed_rules = list_operator_rules(site_id, statuses=["confirmed"], active_only=True, limit=100)
        signals = {
            "predicted_drinks": predicted_drinks,
            "predicted_cents": predicted_cents,
            "rush_windows": [
                {"start": rw.get("start"), "end": rw.get("end"),
                 "predicted_drinks": rw.get("predicted_drinks"), "band": "peak"}
                for rw in rush_windows
            ],
            **labor,
        }
        actions = generate_actions(signals, confirmed_rules=confirmed_rules, forecast_date=plan_date)
    except Exception:
        pass

    return {
        "meta": {
            "prediction_id": prediction.get("prediction_id"),
            "forecast_date": forecast.get("forecast_date"),
            "day_name": forecast.get("day_name"),
            "generated_at": str(row.get("generated_at", "")),
            "staffing_mode": forecast.get("staffing_mode") or prediction.get("staffing_mode"),
            "confidence": confidence,
            "confidence_label": confidence_label,
            "staff_scheduled": forecast.get("staff_scheduled") or prediction.get("staff_scheduled"),
        },
        "forecast": {
            "total_predicted_drinks": predicted_drinks,
            "total_predicted_workload": forecast.get("total_predicted_workload")
            or prediction.get("total_predicted_workload"),
            "event_multiplier": prediction.get("event_multiplier", 1.0),
            "predicted_revenue_cents": predicted_cents,
        },
        "labor": {
            "scheduled_labor_cents": labor["scheduled_labor_cents"],
            "wage_pct": labor["wage_pct"],
            "labor_risk": labor["labor_risk"],
            "staff_count": labor["staff_count"],
            "total_hours": labor["total_hours"],
        },
        "weather": {
            "temp_c": weather.get("temp_c"),
            "description": weather.get("description"),
            "rain_probability": weather.get("rain_probability"),
            "humidity": weather.get("humidity"),
        },
        "actions": actions,
        "rush_windows": rush_response,
        "hourly": hourly_response,
        "peak_trade": peak_trade,
    }
