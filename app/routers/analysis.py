from fastapi import APIRouter, Depends, Query
from datetime import date
from typing import Optional

from app.dependencies import get_validated_site
from analysis.accuracy import get_rolling_accuracy, get_adoption_metrics
from analysis.reporting import generate_weekly_review, generate_weekly_roi_report
from data.storage import get_staffing_variance_intervals

router = APIRouter(prefix="/api/sites/{site_id}/analysis", tags=["analysis"])


@router.get("/accuracy")
def accuracy(
    site: dict = Depends(get_validated_site),
    days_back: int = Query(default=7, ge=1, le=90),
    reference_date: Optional[date] = Query(default=None),
):
    return get_rolling_accuracy(
        site_id=site["site_id"],
        days_back=days_back,
        reference_date=reference_date,
    )


@router.get("/adoption")
def adoption(
    site: dict = Depends(get_validated_site),
    start_date: date = Query(...),
    end_date: date = Query(...),
):
    return get_adoption_metrics(
        site_id=site["site_id"],
        start_date=start_date,
        end_date=end_date,
    )


@router.get("/weekly-review")
def weekly_review(
    site: dict = Depends(get_validated_site),
    week_end: Optional[date] = Query(default=None),
):
    return generate_weekly_review(
        site_id=site["site_id"],
        site_name=site["name"],
        week_end=week_end,
    )


@router.get("/weekly-roi")
def weekly_roi(
    site: dict = Depends(get_validated_site),
    week_end: Optional[date] = Query(default=None),
):
    return generate_weekly_roi_report(
        site_id=site["site_id"],
        site_name=site["name"],
        week_end=week_end,
    )


@router.get("/staffing-variance")
def staffing_variance(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None),
):
    return get_staffing_variance_intervals(
        site_id=site["site_id"],
        target_date=target_date or date.today(),
    )
