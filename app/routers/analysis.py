from fastapi import APIRouter, Depends, Query
from datetime import date
from typing import Optional

from app.auth import get_current_user, require_role
from app.dependencies import get_validated_site
from app.schemas import (
    InventoryCountRequest,
    InventoryItemUpsertRequest,
    InventoryUsageRuleUpsertRequest,
)
from analysis.business_intelligence import compute_business_intelligence
from analysis.menu_engineering import compute_menu_matrix
from analysis.accuracy import (
    get_rolling_accuracy,
    get_accuracy_dashboard,
    get_adoption_metrics,
    get_daily_loop_status,
)
from analysis.next_actions import generate_next_actions, persist_next_actions
from analysis.shift_optimizer import optimize_shifts, optimize_shifts_range
from analysis.workflow import analyze_workflow, generate_roster_change_plan
from analysis.reporting import generate_weekly_review, generate_weekly_roi_report
from data.storage import (
    bootstrap_default_inventory_rules,
    get_bottom_line_scorecard,
    get_day_ingest_diagnostics,
    get_data_quality_flags,
    backfill_realized_impacts,
    resolve_data_quality_flag,
    upsert_data_quality_flag,
    get_data_health,
    get_daily_efficiency_snapshot,
    get_inventory_alerts,
    get_inventory_usage_patterns,
    list_inventory_items,
    list_inventory_usage_rules,
    get_pipeline_health,
    get_recent_pipeline_runs,
    get_staffing_variance_intervals,
    store_inventory_count,
    upsert_inventory_item,
    upsert_inventory_usage_rule,
)

router = APIRouter(prefix="/api/sites/{site_id}/analysis", tags=["analysis"])


@router.get("/business-intelligence")
def business_intelligence(
    site: dict = Depends(get_validated_site),
    days: int = Query(default=28, ge=7, le=90),
):
    return compute_business_intelligence(site_id=site["site_id"], days=days)


@router.get("/menu-engineering")
def menu_engineering(
    site: dict = Depends(get_validated_site),
    days: int = Query(default=28, ge=7, le=90),
):
    return compute_menu_matrix(site_id=site["site_id"], days=days)


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


@router.get("/accuracy-dashboard")
def accuracy_dashboard(
    site: dict = Depends(get_validated_site),
    days_back: int = Query(default=30, ge=7, le=90),
):
    return get_accuracy_dashboard(site["site_id"], days_back=days_back)


@router.get("/daily-loop-status")
def daily_loop_status(site: dict = Depends(get_validated_site)):
    return get_daily_loop_status(site["site_id"])


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
    _user: dict = Depends(require_role("MANAGER")),
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


@router.get("/bottom-line-scorecard")
def bottom_line_scorecard(
    site: dict = Depends(get_validated_site),
    days: int = Query(default=30, ge=7, le=120),
    compare_days: int = Query(default=7, ge=3, le=28),
):
    return get_bottom_line_scorecard(
        site_id=site["site_id"],
        days=days,
        compare_days=compare_days,
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


@router.get("/inventory/items")
def inventory_items(
    site: dict = Depends(get_validated_site),
    active_only: bool = Query(default=True),
):
    return {
        "site_id": site["site_id"],
        "items": list_inventory_items(site["site_id"], active_only=active_only),
    }


@router.post("/inventory/items")
def inventory_item_upsert(
    payload: InventoryItemUpsertRequest,
    site: dict = Depends(get_validated_site),
):
    inventory_item_id = upsert_inventory_item(
        site_id=site["site_id"],
        item_name=payload.item_name,
        score_key=payload.score_key,
        unit=payload.unit,
        reorder_point=payload.reorder_point,
        par_level=payload.par_level,
        lead_time_days=payload.lead_time_days,
        active=payload.active,
        metadata=payload.metadata,
    )
    return {"status": "ok", "site_id": site["site_id"], "inventory_item_id": inventory_item_id}


@router.post("/inventory/items/{inventory_item_id}/count")
def inventory_item_count(
    inventory_item_id: str,
    payload: InventoryCountRequest,
    site: dict = Depends(get_validated_site),
):
    count_id = store_inventory_count(
        site_id=site["site_id"],
        inventory_item_id=inventory_item_id,
        quantity_on_hand=payload.quantity_on_hand,
        counted_at=payload.counted_at,
        source=payload.source,
        notes=payload.notes,
    )
    alerts = get_inventory_alerts(site["site_id"], lookback_days=30, include_ok=False)
    item_alert = next(
        (a for a in alerts if a.get("inventory_item_id") == inventory_item_id),
        None,
    )
    return {
        "status": "ok",
        "site_id": site["site_id"],
        "count_id": count_id,
        "inventory_item_id": inventory_item_id,
        "alert": item_alert,
    }


@router.get("/inventory/rules")
def inventory_rules(
    site: dict = Depends(get_validated_site),
    active_only: bool = Query(default=True),
):
    return {
        "site_id": site["site_id"],
        "rules": list_inventory_usage_rules(site["site_id"], active_only=active_only),
    }


@router.post("/inventory/rules")
def inventory_rule_upsert(
    payload: InventoryUsageRuleUpsertRequest,
    site: dict = Depends(get_validated_site),
):
    rule_id = upsert_inventory_usage_rule(
        site_id=site["site_id"],
        inventory_item_id=payload.inventory_item_id,
        trigger_item_name=payload.trigger_item_name,
        units_per_sale=payload.units_per_sale,
        required_modifier_terms=payload.required_modifier_terms,
        excluded_modifier_terms=payload.excluded_modifier_terms,
        priority=payload.priority,
        active=payload.active,
    )
    return {"status": "ok", "site_id": site["site_id"], "rule_id": rule_id}


@router.post("/inventory/bootstrap-default-rules")
def inventory_bootstrap_defaults(site: dict = Depends(get_validated_site)):
    summary = bootstrap_default_inventory_rules(site["site_id"])
    return {"status": "ok", "site_id": site["site_id"], **summary}


@router.get("/inventory/alerts")
def inventory_alerts(
    site: dict = Depends(get_validated_site),
    lookback_days: int = Query(default=21, ge=7, le=120),
    include_ok: bool = Query(default=False),
):
    alerts = get_inventory_alerts(
        site_id=site["site_id"],
        lookback_days=lookback_days,
        include_ok=include_ok,
    )
    return {
        "site_id": site["site_id"],
        "lookback_days": lookback_days,
        "alerts_total": len(alerts),
        "warning_count": sum(1 for a in alerts if a.get("severity") == "warning"),
        "opportunity_count": sum(1 for a in alerts if a.get("severity") == "opportunity"),
        "alerts": alerts,
    }


@router.get("/inventory/patterns")
def inventory_patterns(
    site: dict = Depends(get_validated_site),
    lookback_days: int = Query(default=30, ge=7, le=180),
    limit: int = Query(default=20, ge=1, le=100),
):
    patterns = get_inventory_usage_patterns(
        site_id=site["site_id"],
        lookback_days=lookback_days,
        limit=limit,
    )
    return {
        "site_id": site["site_id"],
        "lookback_days": lookback_days,
        "patterns_total": len(patterns),
        "patterns": patterns,
    }


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
    persist: bool = Query(default=True),
    max_actions: int = Query(default=8, ge=1, le=20),
):
    td = target_date or date.today()
    payload = generate_next_actions(
        site_id=site["site_id"],
        target_date=td,
        max_actions=max_actions,
    )
    # Always persist so each action gets a stable rec_id for feedback buttons
    if payload.get("actions"):
        persistence = persist_next_actions(
            site_id=site["site_id"],
            actions=payload["actions"],
            target_date=td,
        )
        action_rec_map = persistence.get("action_rec_map", {})
        for action in payload["actions"]:
            action["rec_id"] = action_rec_map.get(action.get("action_key"))
        payload["persistence"] = persistence
    return payload


@router.post("/recommendations/feedback")
def recommendation_feedback(
    site: dict = Depends(get_validated_site),
    user: dict = Depends(get_current_user),
    rec_id: str = Query(...),
    adopted: bool = Query(...),
    notes: str = Query(default=None),
):
    """Record whether a recommendation was acted on."""
    from data.storage import store_adoption_log
    log_id = store_adoption_log(
        site_id=site["site_id"],
        log_date=date.today(),
        rec_id=rec_id,
        manager_name=user.get("name") or user.get("sub"),
        adopted=adopted,
        notes=notes,
    )
    return {"log_id": log_id, "rec_id": rec_id, "adopted": adopted}


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
    _user: dict = Depends(require_role("MANAGER")),
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
    _user: dict = Depends(require_role("MANAGER")),
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
