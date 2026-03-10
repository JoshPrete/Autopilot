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
                "hour_label": f"{hour}am" if hour < 12 else (f"{hour - 12}pm" if hour > 12 else "12pm"),
                "predicted_workload": h.get("predicted_workload"),
                "is_rush": h.get("is_rush", False),
            }
        )

    site_id = site["site_id"]
    predicted_drinks = (
        forecast.get("total_predicted_drinks") or prediction.get("total_predicted_drinks") or 0
    )

    # ── Revenue forecast (intelligence layer) ─────────────────────────────────
    revenue_signals: dict = {}
    try:
        from data.storage import get_daily_profitability
        history = get_daily_profitability(site_id, plan_date - timedelta(days=28), plan_date)
        normalized_history = [
            {
                "date": r.get("profit_date") or plan_date,
                "revenue_cents": int(r.get("revenue_cents") or 0),
                "drink_count": int(r.get("drink_count") or 0),
                "labor_cents": int(r.get("labor_cost_cents") or 0),
            }
            for r in (history or [])
        ]
        if normalized_history:
            revenue_signals = predict_revenue(normalized_history, plan_date)
    except Exception:
        pass

    predicted_cents = revenue_signals.get("predicted_cents") or 0

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
    }
