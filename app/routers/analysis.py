from fastapi import APIRouter, Depends, Query
from datetime import date
from typing import Optional

from app.dependencies import get_validated_site
from analysis.accuracy import get_rolling_accuracy, get_adoption_metrics
from analysis.next_actions import generate_next_actions, persist_next_actions
from analysis.shift_optimizer import optimize_shifts, optimize_shifts_range
from analysis.reporting import generate_weekly_review, generate_weekly_roi_report
from data.storage import (
    backfill_realized_impacts,
    get_daily_efficiency_snapshot,
    get_staffing_variance_intervals,
)

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


@router.get("/daily-efficiency")
def daily_efficiency(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None),
):
    return get_daily_efficiency_snapshot(
        site_id=site["site_id"],
        target_date=target_date or date.today(),
    )


@router.get("/recommendations/next-actions")
def next_actions(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None),
    persist: bool = Query(default=False),
    max_actions: int = Query(default=8, ge=1, le=20),
):
    payload = generate_next_actions(
        site_id=site["site_id"],
        target_date=target_date or date.today(),
        max_actions=max_actions,
    )
    if persist and payload.get("actions"):
        payload["persistence"] = persist_next_actions(
            site_id=site["site_id"],
            actions=payload["actions"],
            target_date=target_date or date.today(),
        )
    return payload


@router.post("/recommendations/refresh-realized-impact")
def refresh_realized_impact(
    site: dict = Depends(get_validated_site),
    lookback_days: int = Query(default=120, ge=7, le=365),
    window_days: int = Query(default=7, ge=3, le=21),
    limit: int = Query(default=100, ge=1, le=500),
):
    return backfill_realized_impacts(
        site_id=site["site_id"],
        lookback_days=lookback_days,
        window_days=window_days,
        limit=limit,
    )


@router.get("/optimized-shifts")
def optimized_shifts(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None),
    target_wu_per_person: float = Query(default=3.0, ge=1.5, le=6.0),
    min_shift_hours: int = Query(default=3, ge=2, le=8),
    max_shift_hours: int = Query(default=9, ge=4, le=12),
    base_floor_staff: int = Query(default=1, ge=0, le=3),
):
    return optimize_shifts(
        site_id=site["site_id"],
        target_date=target_date or date.today(),
        target_wu_per_person=target_wu_per_person,
        min_shift_hours=min_shift_hours,
        max_shift_hours=max_shift_hours,
        base_floor_staff=base_floor_staff,
    )


@router.get("/optimized-shifts-range")
def optimized_shifts_range(
    site: dict = Depends(get_validated_site),
    start_date: Optional[date] = Query(default=None),
    days: int = Query(default=28, ge=7, le=56),
    target_wu_per_person: float = Query(default=3.0, ge=1.5, le=6.0),
    min_shift_hours: int = Query(default=3, ge=2, le=8),
    max_shift_hours: int = Query(default=9, ge=4, le=12),
    base_floor_staff: int = Query(default=1, ge=0, le=3),
):
    return optimize_shifts_range(
        site_id=site["site_id"],
        start_date=start_date or date.today(),
        days=days,
        target_wu_per_person=target_wu_per_person,
        min_shift_hours=min_shift_hours,
        max_shift_hours=max_shift_hours,
        base_floor_staff=base_floor_staff,
    )
