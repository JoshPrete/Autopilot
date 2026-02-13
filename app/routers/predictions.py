from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_validated_site
from app.schemas import GeneratePredictionRequest
from data.storage import get_prediction
from models.prediction import generate_prediction

router = APIRouter(prefix="/api/sites/{site_id}/predictions", tags=["predictions"])


@router.get("/today")
def get_today_prediction(site: dict = Depends(get_validated_site)):
    prediction = get_prediction(site["site_id"], date.today())
    if not prediction:
        raise HTTPException(status_code=404, detail="No prediction for today")
    return prediction


@router.post("/generate")
def create_prediction(body: GeneratePredictionRequest, site: dict = Depends(get_validated_site)):
    target = body.target_date or date.today()
    result = generate_prediction(
        site_id=site["site_id"],
        target_date=target,
        staff_scheduled=body.staff_scheduled,
        save=body.save,
    )
    return result
