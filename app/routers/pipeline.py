from datetime import date

from fastapi import APIRouter, Depends

from app.dependencies import get_validated_site
from app.schemas import IngestRequest, PredictRequest
from scripts.daily_autopilot import step_ingest, step_predict

router = APIRouter(prefix="/api/sites/{site_id}/pipeline", tags=["pipeline"])


@router.post("/ingest")
def run_ingest(body: IngestRequest, site: dict = Depends(get_validated_site)):
    result = step_ingest(
        site_id=site["site_id"],
        run_date=body.run_date or date.today(),
        dry_run=body.dry_run,
    )
    return result


@router.post("/predict")
def run_predict(body: PredictRequest, site: dict = Depends(get_validated_site)):
    result = step_predict(
        site_id=site["site_id"],
        site_name=site["name"],
        run_date=body.run_date or date.today(),
        staff_scheduled=body.staff_scheduled,
        staff_names=body.staff_names,
        dry_run=body.dry_run,
    )
    return result
