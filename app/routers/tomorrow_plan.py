import json
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.dependencies import get_validated_site
from data.storage import get_prediction
from delivery.tomorrow_plan import generate_tomorrow_plan, generate_tomorrow_plan_html

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
