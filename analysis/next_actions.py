"""
Next-actions recommendation engine.

Turns daily efficiency + item margin context into concrete operator actions
with estimated impact, then optionally persists them into recommendations.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from data.storage import (
    backfill_realized_impacts,
    get_action_type_outcome_summary,
    get_data_health,
    get_daily_efficiency_snapshot,
    recommendation_exists_for_action_key,
    store_recommendation,
)
from analysis.profitability import compute_item_margins
from analysis.workflow import analyze_workflow

logger = logging.getLogger("autopilot.next_actions")


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _confidence_with_memory(site_id: str, action_type: str, base: float) -> tuple[float, dict]:
    """
    Adjust confidence with historical adoption outcomes for this action type.
    """
    stats = get_action_type_outcome_summary(site_id, action_type, days=90)
    rate = stats.get("adoption_rate")
    proven_weekly = stats.get("avg_realized_weekly_profit_delta_cents")
    adoption_term = ((rate - 0.5) * 0.35) if rate is not None else 0.0
    # +$1,000 weekly proven impact ~= +0.15 confidence, capped.
    proven_term = _clamp((proven_weekly or 0) / 1_000_000, -0.18, 0.18)
    adjusted = _clamp(base + adoption_term + proven_term, 0.35, 0.95)
    return round(adjusted, 2), stats


def _rank_score(action: dict) -> int:
    """
    Composite ranking:
      expected impact + weighted proven historical impact for action type.
    """
    expected = int(action.get("expected_weekly_profit_uplift_cents") or 0)
    proven = int(action.get("proven_weekly_impact_cents") or 0)
    return round(expected + (proven * 0.7))


def _apply_data_health_gate(action: dict, data_health: dict | None) -> dict:
    """
    Gate recommendation confidence/notes when source data is weak.
    """
    if not isinstance(data_health, dict):
        return action
    status = data_health.get("status")
    if status == "green":
        return action

    gated = dict(action)
    base_conf = float(gated.get("confidence") or 0.6)
    if status == "yellow":
        gated["confidence"] = round(_clamp(base_conf - 0.08, 0.35, 0.95), 2)
    elif status == "red":
        gated["confidence"] = round(_clamp(base_conf - 0.22, 0.25, 0.9), 2)
    gated["data_health_status"] = status
    gated["gated_reason"] = "Recommendation confidence reduced due to stale/incomplete source data."
    return gated


def generate_next_actions(site_id: str, target_date: date | None = None, max_actions: int = 8) -> dict:
    """
    Generate ranked action recommendations from current operational data.
    """
    target_date = target_date or date.today()
    # Keep recommendation memory fresh as part of generation path.
    impact_refresh = backfill_realized_impacts(site_id=site_id, lookback_days=120, window_days=7, limit=50)
    data_health = get_data_health(site_id)
    snapshot = get_daily_efficiency_snapshot(site_id, target_date)
    summary = snapshot.get("summary", {})
    intervals = snapshot.get("intervals", [])
    margins = compute_item_margins(site_id, days=30)
    workflow = analyze_workflow(site_id, target_date)

    actions: list[dict] = []
    labor_cost_per_hour = 0.0
    if (summary.get("deputy_staff_hours") or 0) > 0:
        labor_cost_per_hour = (summary.get("deputy_labor_cost_cents", 0) or 0) / summary["deputy_staff_hours"]

    # 1) Staffing optimization actions from interval mismatches.
    understaffed = sorted(
        [r for r in intervals if r.get("status") == "understaffed" and (r.get("revenue_cents", 0) > 0)],
        key=lambda r: (r.get("revenue_cents", 0), r.get("workload_units", 0)),
        reverse=True,
    )
    if understaffed:
        top = understaffed[0]
        interval_revenue = int(top.get("revenue_cents", 0) or 0)
        est_weekly_uplift = round(interval_revenue * 0.10 * 5)  # repeated block, 5 similar days/wk estimate
        confidence, memory = _confidence_with_memory(site_id, "ADD_STAFF_BLOCK", 0.66)
        actions.append(
            {
                "action_key": f"add_staff_{target_date.isoformat()}_{str(top.get('interval_start'))}",
                "action_type": "ADD_STAFF_BLOCK",
                "title": "Add 1 staff during peak understaffed block",
                "reason": "Highest-revenue interval is understaffed (high workload per person).",
                "window_start": str(top.get("interval_start")),
                "window_length_minutes": 15,
                "workflow_mode_hint": "3p_or_4p",
                "roles_hint": ["P1 front", "P2 shots", "P3 finish/delivery"],
                "expected_weekly_profit_uplift_cents": est_weekly_uplift,
                "expected_weekly_labor_change_cents": round(labor_cost_per_hour * 1.5),
                "proven_weekly_impact_cents": memory.get("avg_realized_weekly_profit_delta_cents"),
                "confidence": confidence,
                "memory": memory,
            }
        )

    overstaffed = sorted(
        [r for r in intervals if r.get("status") == "overstaffed"],
        key=lambda r: (-(r.get("revenue_cents", 0)), r.get("staff_delta", 0)),
    )
    if overstaffed:
        top = overstaffed[0]
        est_weekly_savings = round((labor_cost_per_hour * 1.0) * 4)  # 1hr trimmed x4 days as baseline
        confidence, memory = _confidence_with_memory(site_id, "CUT_STAFF_BLOCK", 0.62)
        actions.append(
            {
                "action_key": f"cut_staff_{target_date.isoformat()}_{str(top.get('interval_start'))}",
                "action_type": "CUT_STAFF_BLOCK",
                "title": "Trim 1 staff-hour in low-demand overstaffed block",
                "reason": "Interval shows low workload per person with excess staffing.",
                "window_start": str(top.get("interval_start")),
                "window_length_minutes": 60,
                "workflow_mode_hint": "2p_or_3p",
                "expected_weekly_profit_uplift_cents": est_weekly_savings,
                "expected_weekly_labor_change_cents": -est_weekly_savings,
                "proven_weekly_impact_cents": memory.get("avg_realized_weekly_profit_delta_cents"),
                "confidence": confidence,
                "memory": memory,
            }
        )

    # 1.5) Workflow bottleneck actions from role-level ramification model.
    high_impact_intervals = workflow.get("high_impact_intervals", []) if isinstance(workflow, dict) else []
    if high_impact_intervals:
        top_wf = high_impact_intervals[0]
        bottleneck = (top_wf.get("bottleneck") or {}).get("type")
        observed = top_wf.get("observed") or {}
        scenarios = top_wf.get("scenarios") or []
        best = max(
            scenarios,
            key=lambda s: int(s.get("estimated_net_delta_cents") or 0),
            default=None,
        )
        if best and bottleneck and bottleneck != "none":
            confidence, memory = _confidence_with_memory(site_id, "WORKFLOW_SHIFT_REALLOC", 0.64)
            estimated_net = int(best.get("estimated_net_delta_cents") or 0) * 5
            title = f"Reallocate roles to relieve {bottleneck.replace('_', ' ')}"
            reason = (
                f"Observed {observed.get('staff_on', 0)} staff at {top_wf.get('interval_start')}; "
                f"workflow model indicates better outcome around {best.get('staff_count')} staff."
            )
            actions.append(
                {
                    "action_key": f"workflow_{target_date.isoformat()}_{str(top_wf.get('interval_start'))}",
                    "action_type": "WORKFLOW_SHIFT_REALLOC",
                    "title": title,
                    "reason": reason,
                    "window_start": str(top_wf.get("interval_start")),
                    "window_length_minutes": 30,
                    "bottleneck_type": bottleneck,
                    "workflow_mode_hint": f"{best.get('staff_count')}p",
                    "roles_hint": ["P1 greet/order", "P2 shots", "P3 finish", "P4 delivery/support"],
                    "expected_weekly_profit_uplift_cents": estimated_net,
                    "expected_weekly_labor_change_cents": int(best.get("labor_delta_cents") or 0) * 5,
                    "proven_weekly_impact_cents": memory.get("avg_realized_weekly_profit_delta_cents"),
                    "confidence": confidence,
                    "memory": memory,
                }
            )

    # 2) Margin actions from item-level profitability.
    low_margin_high_volume = [
        m for m in margins
        if (m.get("margin_pct") is not None and m["margin_pct"] < 58 and (m.get("qty") or 0) >= 20)
    ]
    low_margin_high_volume = sorted(low_margin_high_volume, key=lambda m: m.get("qty", 0), reverse=True)
    if low_margin_high_volume:
        item = low_margin_high_volume[0]
        weekly_qty = max(1, round((item.get("qty", 0) / 30) * 7))
        uplift = weekly_qty * 50  # +$0.50 trial price increase
        confidence, memory = _confidence_with_memory(site_id, "PRICE_TEST_UP", 0.58)
        actions.append(
            {
                "action_key": f"price_test_{target_date.isoformat()}_{item.get('score_key')}",
                "action_type": "PRICE_TEST_UP",
                "title": f"Run +$0.50 price test on {item.get('item', 'item')}",
                "reason": "High volume with below-target margin suggests pricing headroom test.",
                "item": item.get("item"),
                "score_key": item.get("score_key"),
                "estimated_weekly_units": weekly_qty,
                "expected_weekly_profit_uplift_cents": uplift,
                "expected_weekly_labor_change_cents": 0,
                "proven_weekly_impact_cents": memory.get("avg_realized_weekly_profit_delta_cents"),
                "confidence": confidence,
                "memory": memory,
            }
        )

    # Rebuild list with gated actions and filter extremely weak actions in red-health mode.
    gated_actions = [_apply_data_health_gate(a, data_health) for a in actions]
    if isinstance(data_health, dict) and data_health.get("status") == "red":
        gated_actions = [a for a in gated_actions if float(a.get("confidence") or 0) >= 0.45]

    for action in gated_actions:
        action["ranking_score_cents"] = _rank_score(action)
        action["data_health"] = {
            "status": data_health.get("status") if isinstance(data_health, dict) else None,
            "score": data_health.get("score") if isinstance(data_health, dict) else None,
        }

    actions = sorted(gated_actions, key=lambda a: a.get("ranking_score_cents", 0), reverse=True)[:max_actions]

    return {
        "site_id": site_id,
        "target_date": target_date.isoformat(),
        "summary": {
            "actions_generated": len(actions),
            "revenue_per_labor_hour_cents": summary.get("revenue_per_labor_hour_cents"),
            "labor_pct": summary.get("labor_pct"),
            "data_health_status": data_health.get("status") if isinstance(data_health, dict) else None,
            "data_health_score": data_health.get("score") if isinstance(data_health, dict) else None,
            "impact_refresh": impact_refresh,
        },
        "actions": actions,
    }


def persist_next_actions(site_id: str, actions: list[dict], target_date: date | None = None) -> dict:
    """
    Persist actions to recommendations table (idempotent by action_key/day).
    """
    target_date = target_date or date.today()
    stored = 0
    skipped = 0
    rec_ids: list[str] = []
    now = datetime.utcnow()

    for action in actions:
        action_key = action.get("action_key")
        action_type = action.get("action_type", "NEXT_ACTION")
        if action_key and recommendation_exists_for_action_key(site_id, action_type, action_key, target_date):
            skipped += 1
            continue
        rec_id = store_recommendation(
            prediction_id=None,
            site_id=site_id,
            action_type=action_type,
            action_timing=now,
            owner_role="MANAGER",
            action_details=action,
        )
        rec_ids.append(rec_id)
        stored += 1

    logger.info("Persisted next actions for %s: stored=%d skipped=%d", site_id, stored, skipped)
    return {"stored": stored, "skipped": skipped, "rec_ids": rec_ids}
