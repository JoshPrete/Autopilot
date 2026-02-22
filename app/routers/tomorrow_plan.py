from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from app.dependencies import get_validated_site
from data.storage import get_prediction
from delivery.tomorrow_plan import generate_tomorrow_plan, generate_tomorrow_plan_html

router = APIRouter(prefix="/api/sites/{site_id}/tomorrow-plan", tags=["tomorrow-plan"])


def _get_prediction_or_404(site: dict) -> dict:
    prediction = get_prediction(site["site_id"], date.today())
    if not prediction:
        raise HTTPException(status_code=404, detail="No prediction for today")
    return prediction


@router.get("/text")
def tomorrow_plan_text(
    site: dict = Depends(get_validated_site),
    staff_names: str = Query(default=None, description="JSON-encoded staff_names dict"),
):
    prediction = _get_prediction_or_404(site)
    import json

    names = json.loads(staff_names) if staff_names else None
    plan = generate_tomorrow_plan(
        site_name=site["name"],
        site_id=site["site_id"],
        prediction=prediction,
        staff_names=names,
    )
    return {"plan": plan}


@router.get("/html")
def tomorrow_plan_html(
    site: dict = Depends(get_validated_site),
    staff_names: str = Query(default=None, description="JSON-encoded staff_names dict"),
):
    prediction = _get_prediction_or_404(site)
    import json

    names = json.loads(staff_names) if staff_names else None
    html = generate_tomorrow_plan_html(
        site_name=site["name"],
        site_id=site["site_id"],
        prediction=prediction,
        staff_names=names,
    )
    return HTMLResponse(content=html)
