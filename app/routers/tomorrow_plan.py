import json
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.dependencies import get_validated_site
from data.prediction_utils import normalize_prediction_record
from data.storage import get_prediction
from delivery.tomorrow_plan import generate_tomorrow_plan, generate_tomorrow_plan_html
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
            "total_predicted_drinks": forecast.get("total_predicted_drinks")
            or prediction.get("total_predicted_drinks"),
            "total_predicted_workload": forecast.get("total_predicted_workload")
            or prediction.get("total_predicted_workload"),
            "event_multiplier": prediction.get("event_multiplier", 1.0),
        },
        "weather": {
            "temp_c": weather.get("temp_c"),
            "description": weather.get("description"),
            "rain_probability": weather.get("rain_probability"),
            "humidity": weather.get("humidity"),
        },
        "rush_windows": rush_response,
        "hourly": hourly_response,
    }
