from fastapi import APIRouter, Depends, Query
from datetime import date
from typing import Optional

from app.dependencies import get_validated_site
from analysis.accuracy import get_rolling_accuracy, get_adoption_metrics
from analysis.next_actions import generate_next_actions, persist_next_actions
from analysis.shift_optimizer import optimize_shifts, optimize_shifts_range
from analysis.workflow import analyze_workflow, generate_roster_change_plan
from analysis.reporting import generate_weekly_review, generate_weekly_roi_report
from data.storage import (
    get_day_ingest_diagnostics,
    get_data_quality_flags,
    backfill_realized_impacts,
    resolve_data_quality_flag,
    upsert_data_quality_flag,
    get_data_health,
    get_daily_efficiency_snapshot,
    get_pipeline_health,
    get_recent_pipeline_runs,
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


@router.get("/data-health")
def data_health(site: dict = Depends(get_validated_site)):
    return get_data_health(site_id=site["site_id"])


@router.get("/pipeline-runs")
def pipeline_runs(
    site: dict = Depends(get_validated_site),
    limit: int = Query(default=30, ge=1, le=200),
    job_name: Optional[str] = Query(default=None),
):
    return {
        "site_id": site["site_id"],
        "runs": get_recent_pipeline_runs(
            site_id=site["site_id"],
            limit=limit,
            job_name=job_name,
        ),
    }


@router.get("/pipeline-health")
def pipeline_health(
    site: dict = Depends(get_validated_site),
    hours: int = Query(default=24, ge=1, le=24 * 30),
):
    return get_pipeline_health(site_id=site["site_id"], hours=hours)


@router.get("/data-quality/day-diagnostics")
def data_quality_day_diagnostics(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None),
):
    return get_day_ingest_diagnostics(site["site_id"], target_date or date.today())


@router.get("/data-quality/flags")
def data_quality_flags(
    site: dict = Depends(get_validated_site),
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    active_only: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=1000),
):
    return {
        "site_id": site["site_id"],
        "flags": get_data_quality_flags(
            site_id=site["site_id"],
            start_date=start_date,
            end_date=end_date,
            active_only=active_only,
            limit=limit,
        ),
    }


@router.post("/data-quality/flags/manual-exclude")
def add_manual_forecast_exclusion(
    site: dict = Depends(get_validated_site),
    flag_date: date = Query(...),
    reason: str = Query(default="Manually excluded from forecast baseline"),
):
    flag_id = upsert_data_quality_flag(
        site_id=site["site_id"],
        flag_date=flag_date,
        flag_type="manual_exclude_forecast",
        severity="high",
        source="manual",
        reason=reason,
        metadata={"set_via": "api"},
    )
    return {"status": "ok", "flag_id": flag_id, "flag_date": flag_date.isoformat()}


@router.delete("/data-quality/flags/manual-exclude")
def remove_manual_forecast_exclusion(
    site: dict = Depends(get_validated_site),
    flag_date: date = Query(...),
):
    resolved = resolve_data_quality_flag(
        site_id=site["site_id"],
        flag_date=flag_date,
        flag_type="manual_exclude_forecast",
        source="manual",
    )
    return {"status": "ok", "resolved": resolved, "flag_date": flag_date.isoformat()}


@router.post("/data-quality/flags/partial-ingest")
def add_partial_ingest_flag(
    site: dict = Depends(get_validated_site),
    flag_date: date = Query(...),
    reason: str = Query(default="Manual partial-ingest flag"),
):
    flag_id = upsert_data_quality_flag(
        site_id=site["site_id"],
        flag_date=flag_date,
        flag_type="partial_ingest",
        severity="high",
        source="manual",
        reason=reason,
        metadata={"set_via": "api"},
    )
    return {"status": "ok", "flag_id": flag_id, "flag_date": flag_date.isoformat()}


@router.delete("/data-quality/flags/partial-ingest")
def remove_partial_ingest_flag(
    site: dict = Depends(get_validated_site),
    flag_date: date = Query(...),
):
    resolved = resolve_data_quality_flag(
        site_id=site["site_id"],
        flag_date=flag_date,
        flag_type="partial_ingest",
        source="manual",
    )
    # Also clear system flag if operator has verified day integrity.
    resolved += resolve_data_quality_flag(
        site_id=site["site_id"],
        flag_date=flag_date,
        flag_type="partial_ingest",
        source="system",
    )
    return {"status": "ok", "resolved": resolved, "flag_date": flag_date.isoformat()}


@router.get("/workflow")
def workflow(
    site: dict = Depends(get_validated_site),
    target_date: Optional[date] = Query(default=None),
):
    return analyze_workflow(site_id=site["site_id"], target_date=target_date or date.today())


@router.get("/workflow/roster-plan")
def workflow_roster_plan(
    site: dict = Depends(get_validated_site),
    start_date: Optional[date] = Query(default=None),
    days: int = Query(default=14, ge=7, le=56),
    target_wu_per_person: float = Query(default=3.0, ge=1.5, le=6.0),
    min_shift_hours: int = Query(default=3, ge=2, le=8),
    max_shift_hours: int = Query(default=9, ge=4, le=12),
    base_floor_staff: int = Query(default=1, ge=0, le=3),
):
    return generate_roster_change_plan(
        site_id=site["site_id"],
        start_date=start_date or date.today(),
        days=days,
        target_wu_per_person=target_wu_per_person,
        min_shift_hours=min_shift_hours,
        max_shift_hours=max_shift_hours,
        base_floor_staff=base_floor_staff,
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
