"""
Next-actions recommendation engine.

Turns daily efficiency + item margin context into concrete operator actions
with estimated impact, then optionally persists them into recommendations.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from config.constants import LABOR_PCT_TARGET_HIGH, LABOR_PCT_TARGET_LOW
from data.storage import (
    backfill_realized_impacts,
    get_action_type_outcome_summary,
    get_bottom_line_scorecard,
    get_data_health,
    get_daily_efficiency_snapshot,
    recommendation_exists_for_action_key,
    store_recommendation,
)
from analysis.profitability import compute_item_margins
from analysis.workflow import analyze_workflow

logger = logging.getLogger("autopilot.next_actions")
PROVEN_IMPACT_MIN_REALIZED_SAMPLES = 2
SERVICE_RISK_CAP_RATIO = 0.12
OVERSTAFFED_WASTE_RATIO_TRIGGER = 0.18
PHASE_MIN_WORKING_INTERVALS = 8


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


def _phase_priority_bonus(action: dict, optimization_phase: str) -> int:
    """
    Strategy switch:
      - labor_efficiency mode prioritizes labor-saving and bottleneck fixes.
      - revenue_growth mode prioritizes throughput/revenue levers.
    """
    action_type = action.get("action_type")
    labor_delta = int(action.get("expected_weekly_labor_change_cents") or 0)
    profit_uplift = int(action.get("expected_weekly_profit_uplift_cents") or 0)

    if optimization_phase == "labor_efficiency":
        if action_type == "CUT_STAFF_BLOCK":
            return 2200
        if action_type == "WORKFLOW_SHIFT_REALLOC":
            return 1400 if labor_delta < 0 else 550
        if action_type == "ADD_STAFF_BLOCK":
            return -250
        if action_type == "PRICE_TEST_UP":
            return -700
        return 0

    if action_type == "PRICE_TEST_UP":
        return 2200
    if action_type == "ADD_STAFF_BLOCK":
        return 950
    if action_type == "WORKFLOW_SHIFT_REALLOC":
        return 800 if profit_uplift > 0 else 300
    if action_type == "CUT_STAFF_BLOCK":
        return -2600
    return 0


def _profitability_priority_bonus(action: dict, profitability_targets: dict | None) -> int:
    if not isinstance(profitability_targets, dict):
        return 0

    focus = str((profitability_targets.get("primary_lever") or {}).get("focus") or "").strip()
    gaps = profitability_targets.get("gaps") or {}
    action_type = action.get("action_type")
    labor_delta = int(action.get("expected_weekly_labor_change_cents") or 0)
    profit_uplift = int(action.get("expected_weekly_profit_uplift_cents") or 0)

    labor_gap = int(gaps.get("weekly_labor_reduction_needed_cents") or 0)
    cogs_gap = int(gaps.get("weekly_cogs_reduction_needed_cents") or 0)
    prime_gap = int(gaps.get("weekly_prime_cost_reduction_needed_cents") or 0)
    revenue_gap = int(gaps.get("weekly_revenue_needed_for_net_margin_target_cents") or 0)

    if focus == "labor_efficiency":
        if action_type == "CUT_STAFF_BLOCK":
            return 1800 + min(3200, labor_gap // 25)
        if action_type == "WORKFLOW_SHIFT_REALLOC":
            return 1200 if labor_delta < 0 else 300
        if action_type == "PRICE_TEST_UP":
            return -900
        return 0

    if focus == "cogs_control":
        if action_type == "PRICE_TEST_UP":
            return 2800 + min(3800, max(cogs_gap, prime_gap) // 20)
        if action_type == "CUT_STAFF_BLOCK":
            return -1400
        if action_type == "ADD_STAFF_BLOCK":
            return -900
        return 350 if action_type == "WORKFLOW_SHIFT_REALLOC" and profit_uplift > 0 else 0

    if focus == "mixed_margin_repair":
        if action_type == "PRICE_TEST_UP":
            return 1800 + min(2500, cogs_gap // 20)
        if action_type == "CUT_STAFF_BLOCK":
            return 1800 + min(2500, labor_gap // 25)
        if action_type == "WORKFLOW_SHIFT_REALLOC":
            return 900 if profit_uplift > 0 else 250
        return -350 if action_type == "ADD_STAFF_BLOCK" else 0

    if focus == "revenue_growth":
        if action_type == "ADD_STAFF_BLOCK":
            return 1800 + min(3200, revenue_gap // 35)
        if action_type == "WORKFLOW_SHIFT_REALLOC":
            return 1500 if profit_uplift > 0 else 450
        if action_type == "PRICE_TEST_UP":
            return 1200 + min(1800, revenue_gap // 45)
        if action_type == "CUT_STAFF_BLOCK":
            return -1800
        return 0

    return 0


def _focus_gap_details(profitability_targets: dict | None) -> tuple[str | None, int]:
    if not isinstance(profitability_targets, dict):
        return None, 0

    focus = str((profitability_targets.get("primary_lever") or {}).get("focus") or "").strip() or None
    gaps = profitability_targets.get("gaps") or {}

    if focus == "labor_efficiency":
        return "labor gap", int(gaps.get("weekly_labor_reduction_needed_cents") or 0)
    if focus == "cogs_control":
        return "COGS gap", int(gaps.get("weekly_cogs_reduction_needed_cents") or 0)
    if focus == "mixed_margin_repair":
        return "prime-cost gap", int(gaps.get("weekly_prime_cost_reduction_needed_cents") or 0)
    if focus == "revenue_growth":
        return (
            "revenue gap",
            int(gaps.get("weekly_revenue_needed_for_net_margin_target_cents") or 0),
        )
    return None, 0


def _format_cents_compact(cents: int) -> str:
    dollars = abs(int(cents)) / 100
    if dollars >= 1000:
        return f"${dollars:,.0f}/wk"
    return f"${dollars:,.0f}/wk"


def _describe_profitability_alignment(action: dict, profitability_targets: dict | None) -> str | None:
    if not isinstance(profitability_targets, dict):
        return None

    focus = str((profitability_targets.get("primary_lever") or {}).get("focus") or "").strip()
    if not focus:
        return None

    action_type = action.get("action_type")
    labor_delta = int(action.get("expected_weekly_labor_change_cents") or 0)
    gap_label, gap_cents = _focus_gap_details(profitability_targets)
    gap_text = _format_cents_compact(gap_cents) if gap_cents > 0 else None

    if focus == "labor_efficiency":
        if action_type == "CUT_STAFF_BLOCK":
            return (
                f"Targets labor efficiency by directly trimming labor against the remaining "
                f"{gap_label or 'labor gap'} of {gap_text}."
                if gap_text
                else "Targets labor efficiency by directly trimming labor."
            )
        if action_type == "WORKFLOW_SHIFT_REALLOC" and labor_delta < 0:
            return (
                f"Targets labor efficiency by reassigning work while reducing labor against the "
                f"remaining {gap_label or 'labor gap'} of {gap_text}."
                if gap_text
                else "Targets labor efficiency by reassigning work while reducing labor."
            )
        if action_type == "ADD_STAFF_BLOCK":
            return "Protects profitability by avoiding service loss in peak intervals while labor remains the primary constraint."
        return "Secondary lever while labor efficiency remains the main profitability priority."

    if focus == "cogs_control":
        if action_type == "PRICE_TEST_UP":
            return (
                f"Targets COGS control by improving gross margin against the remaining "
                f"{gap_label or 'COGS gap'} of {gap_text}."
                if gap_text
                else "Targets COGS control by improving gross margin."
            )
        return "Secondary lever while COGS control remains the main profitability priority."

    if focus == "mixed_margin_repair":
        if action_type == "PRICE_TEST_UP":
            return "Targets mixed margin repair through price and product-margin improvement."
        if action_type == "CUT_STAFF_BLOCK":
            return "Targets mixed margin repair through labor savings while prime cost remains above target."
        if action_type == "WORKFLOW_SHIFT_REALLOC":
            return "Targets mixed margin repair by improving throughput without proportionate labor growth."
        return "Supports mixed margin repair while prime cost remains above target."

    if focus == "revenue_growth":
        if action_type == "ADD_STAFF_BLOCK":
            return (
                f"Targets revenue growth by protecting throughput against the remaining "
                f"{gap_label or 'revenue gap'} of {gap_text}."
                if gap_text
                else "Targets revenue growth by protecting throughput."
            )
        if action_type == "WORKFLOW_SHIFT_REALLOC":
            return "Targets revenue growth by improving service flow during high-demand windows."
        if action_type == "PRICE_TEST_UP":
            return "Supports revenue growth by lifting contribution margin without adding labor."
        return "Secondary lever while revenue growth remains the main profitability priority."

    return None


def _rank_score(action: dict, optimization_phase: str, profitability_targets: dict | None) -> int:
    """
    Composite ranking:
      expected impact + weighted proven historical impact for action type.
    """
    expected = int(action.get("expected_weekly_profit_uplift_cents") or 0)
    proven = int(action.get("proven_weekly_impact_cents") or 0)
    confidence = float(action.get("confidence") or 0.0)
    realized_samples = int(action.get("realized_samples") or 0)
    proven_weight = 1.1 if realized_samples >= PROVEN_IMPACT_MIN_REALIZED_SAMPLES else 0.35
    confidence_bonus = round(confidence * 1200)
    exploration_penalty = (
        -250 if action.get("proven_gate_status") == "insufficient_realized_history" else 0
    )
    phase_bonus = _phase_priority_bonus(action, optimization_phase)
    profitability_bonus = _profitability_priority_bonus(action, profitability_targets)
    return round(
        expected
        + (proven * proven_weight)
        + confidence_bonus
        + exploration_penalty
        + phase_bonus
        + profitability_bonus
    )


def _staffing_risk_metrics(intervals: list[dict]) -> dict:
    """Compute normalized staffing risk ratios from interval statuses."""
    working = [row for row in intervals if (row.get("status") or "no_workload") != "no_workload"]
    total = len(working)
    if total == 0:
        return {
            "working_intervals": 0,
            "critical_understaffed_intervals": 0,
            "overstaffed_intervals": 0,
            "critical_understaff_ratio": 0.0,
            "overstaffed_ratio": 0.0,
        }

    critical_under = sum(1 for row in working if row.get("status") in ("understaffed", "no_staff"))
    overstaffed = sum(1 for row in working if row.get("status") == "overstaffed")
    return {
        "working_intervals": total,
        "critical_understaffed_intervals": critical_under,
        "overstaffed_intervals": overstaffed,
        "critical_understaff_ratio": round(critical_under / total, 3),
        "overstaffed_ratio": round(overstaffed / total, 3),
    }


def _determine_optimization_phase(summary: dict, risk: dict) -> tuple[str, str]:
    """
    Switch logic:
      1) Reduce labor until efficient frontier.
      2) Once near frontier, shift to revenue growth.
    """
    labor_pct = summary.get("labor_pct")
    if labor_pct is None:
        return "labor_efficiency", "Labor % unavailable; defaulting to labor efficiency mode."

    critical_ratio = float(risk.get("critical_understaff_ratio") or 0.0)
    over_ratio = float(risk.get("overstaffed_ratio") or 0.0)
    working = int(risk.get("working_intervals") or 0)

    if critical_ratio > SERVICE_RISK_CAP_RATIO:
        return (
            "revenue_growth",
            "Service-risk cap exceeded; avoid further labor cuts and prioritize throughput/revenue actions.",
        )
    if labor_pct > LABOR_PCT_TARGET_HIGH:
        return (
            "labor_efficiency",
            f"Labor % above target band ({LABOR_PCT_TARGET_LOW:.0f}-{LABOR_PCT_TARGET_HIGH:.0f}%).",
        )
    if over_ratio >= OVERSTAFFED_WASTE_RATIO_TRIGGER:
        return (
            "labor_efficiency",
            "Overstaffed interval ratio indicates remaining labor waste opportunity.",
        )
    if labor_pct <= LABOR_PCT_TARGET_LOW and over_ratio <= 0.10:
        return (
            "revenue_growth",
            "Labor % at/under efficient floor; shift focus to revenue growth.",
        )
    if (
        working >= PHASE_MIN_WORKING_INTERVALS
        and labor_pct <= LABOR_PCT_TARGET_HIGH
        and over_ratio <= 0.12
    ):
        return (
            "revenue_growth",
            "Labor efficiency near frontier with low overstaffing; prioritize revenue growth.",
        )
    return "labor_efficiency", "Continue labor-efficiency tuning."


def _passes_proven_impact_gate(memory: dict) -> tuple[bool, str]:
    """
    Fail-closed on action types with enough realized samples but non-positive outcomes.
    """
    if not isinstance(memory, dict):
        return True, "insufficient_realized_history"

    realized_count = int(memory.get("realized_count") or 0)
    if realized_count < PROVEN_IMPACT_MIN_REALIZED_SAMPLES:
        return True, "insufficient_realized_history"

    proven_weekly = memory.get("avg_realized_weekly_profit_delta_cents")
    if proven_weekly is None:
        return True, "incomplete_realized_history"

    if float(proven_weekly) <= 0:
        return False, "non_positive_realized_impact"
    return True, "positive_realized_impact"


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


def generate_next_actions(
    site_id: str, target_date: date | None = None, max_actions: int = 8
) -> dict:
    """
    Generate ranked action recommendations from current operational data.
    """
    target_date = target_date or date.today()
    # Keep recommendation memory fresh as part of generation path.
    impact_refresh = backfill_realized_impacts(
        site_id=site_id, lookback_days=120, window_days=7, limit=50
    )
    data_health = get_data_health(site_id)
    snapshot = get_daily_efficiency_snapshot(site_id, target_date)
    summary = snapshot.get("summary", {})
    intervals = snapshot.get("intervals", [])
    margins = compute_item_margins(site_id, days=30)
    workflow = analyze_workflow(site_id, target_date)
    risk_metrics = _staffing_risk_metrics(intervals)
    optimization_phase, phase_reason = _determine_optimization_phase(summary, risk_metrics)
    try:
        scorecard = get_bottom_line_scorecard(site_id, days=30, compare_days=7, top_actions_limit=3)
        profitability_targets = scorecard.get("targets", {}) if isinstance(scorecard, dict) else {}
    except Exception:
        logger.exception("Unable to load profitability targets for next actions")
        profitability_targets = {}

    actions: list[dict] = []
    labor_cost_per_hour = 0.0
    if (summary.get("deputy_staff_hours") or 0) > 0:
        labor_cost_per_hour = (summary.get("deputy_labor_cost_cents", 0) or 0) / summary[
            "deputy_staff_hours"
        ]

    # 1) Staffing optimization actions from interval mismatches.
    understaffed = sorted(
        [
            r
            for r in intervals
            if r.get("status") == "understaffed" and (r.get("revenue_cents", 0) > 0)
        ],
        key=lambda r: (r.get("revenue_cents", 0), r.get("workload_units", 0)),
        reverse=True,
    )
    if understaffed:
        top = understaffed[0]
        interval_revenue = int(top.get("revenue_cents", 0) or 0)
        est_weekly_uplift = round(
            interval_revenue * 0.10 * 5
        )  # repeated block, 5 similar days/wk estimate
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
                "optimization_phase": optimization_phase,
            }
        )

    overstaffed = sorted(
        [r for r in intervals if r.get("status") == "overstaffed"],
        key=lambda r: (-(r.get("revenue_cents", 0)), r.get("staff_delta", 0)),
    )
    if overstaffed:
        top = overstaffed[0]
        est_weekly_savings = round(
            (labor_cost_per_hour * 1.0) * 4
        )  # 1hr trimmed x4 days as baseline
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
                "optimization_phase": optimization_phase,
            }
        )

    # 1.5) Workflow bottleneck actions from role-level ramification model.
    high_impact_intervals = (
        workflow.get("high_impact_intervals", []) if isinstance(workflow, dict) else []
    )
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
                    "roles_hint": [
                        "P1 greet/order",
                        "P2 shots",
                        "P3 finish",
                        "P4 delivery/support",
                    ],
                    "expected_weekly_profit_uplift_cents": estimated_net,
                    "expected_weekly_labor_change_cents": int(best.get("labor_delta_cents") or 0)
                    * 5,
                    "proven_weekly_impact_cents": memory.get(
                        "avg_realized_weekly_profit_delta_cents"
                    ),
                    "confidence": confidence,
                    "memory": memory,
                    "optimization_phase": optimization_phase,
                }
            )

    # 2) Margin actions from item-level profitability.
    low_margin_high_volume = [
        m
        for m in margins
        if (m.get("margin_pct") is not None and m["margin_pct"] < 58 and (m.get("qty") or 0) >= 20)
    ]
    low_margin_high_volume = sorted(
        low_margin_high_volume, key=lambda m: m.get("qty", 0), reverse=True
    )
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
                "optimization_phase": optimization_phase,
            }
        )

    # Rebuild list with gated actions and filter extremely weak actions in red-health mode.
    gated_actions = [_apply_data_health_gate(a, data_health) for a in actions]
    suppressed_by_proven_gate = []
    proven_filtered_actions = []

    for action in gated_actions:
        memory = action.get("memory") if isinstance(action.get("memory"), dict) else {}
        action["realized_samples"] = int((memory or {}).get("realized_count") or 0)
        allow, gate_status = _passes_proven_impact_gate(memory or {})
        action["proven_gate_status"] = gate_status
        if not allow:
            suppressed_by_proven_gate.append(
                {
                    "action_type": action.get("action_type"),
                    "title": action.get("title"),
                    "reason": gate_status,
                    "realized_samples": action.get("realized_samples"),
                    "avg_realized_weekly_profit_delta_cents": action.get(
                        "proven_weekly_impact_cents"
                    ),
                }
            )
            continue
        proven_filtered_actions.append(action)

    gated_actions = proven_filtered_actions
    if isinstance(data_health, dict) and data_health.get("status") == "red":
        gated_actions = [a for a in gated_actions if float(a.get("confidence") or 0) >= 0.45]

    # In revenue_growth mode, suppress pure labor-cut actions when higher-leverage
    # revenue/throughput actions are available.
    if optimization_phase == "revenue_growth":
        preferred = [a for a in gated_actions if a.get("action_type") != "CUT_STAFF_BLOCK"]
        if preferred:
            gated_actions = preferred

    for action in gated_actions:
        profitability_bonus = _profitability_priority_bonus(action, profitability_targets)
        gap_label, focus_gap_cents = _focus_gap_details(profitability_targets)
        action["ranking_score_cents"] = _rank_score(
            action,
            optimization_phase,
            profitability_targets,
        )
        action["profitability_alignment"] = {
            "primary_lever": (
                (profitability_targets.get("primary_lever") or {}).get("focus")
                if isinstance(profitability_targets, dict)
                else None
            ),
            "bonus_cents": profitability_bonus,
            "focus_gap_label": gap_label,
            "focus_gap_cents": focus_gap_cents,
            "reason": _describe_profitability_alignment(action, profitability_targets),
        }
        action["data_health"] = {
            "status": data_health.get("status") if isinstance(data_health, dict) else None,
            "score": data_health.get("score") if isinstance(data_health, dict) else None,
        }

    actions = sorted(gated_actions, key=lambda a: a.get("ranking_score_cents", 0), reverse=True)[
        :max_actions
    ]

    return {
        "site_id": site_id,
        "target_date": target_date.isoformat(),
        "optimization_phase": optimization_phase,
        "phase_reason": phase_reason,
        "profitability_goal": (
            profitability_targets.get("primary_lever") if isinstance(profitability_targets, dict) else {}
        ),
        "profitability_gaps": (
            profitability_targets.get("gaps") if isinstance(profitability_targets, dict) else {}
        ),
        "data_health": {
            "status": data_health.get("status") if isinstance(data_health, dict) else None,
            "score": data_health.get("score") if isinstance(data_health, dict) else None,
        },
        "summary": {
            "actions_generated": len(actions),
            "revenue_per_labor_hour_cents": summary.get("revenue_per_labor_hour_cents"),
            "labor_pct": summary.get("labor_pct"),
            "data_health_status": (
                data_health.get("status") if isinstance(data_health, dict) else None
            ),
            "data_health_score": (
                data_health.get("score") if isinstance(data_health, dict) else None
            ),
            "optimization_phase": optimization_phase,
            "phase_reason": phase_reason,
            "profitability_goal": (
                profitability_targets.get("primary_lever")
                if isinstance(profitability_targets, dict)
                else {}
            ),
            "profitability_gaps": (
                profitability_targets.get("gaps") if isinstance(profitability_targets, dict) else {}
            ),
            "phase_guardrails": {
                "labor_pct_target_low": LABOR_PCT_TARGET_LOW,
                "labor_pct_target_high": LABOR_PCT_TARGET_HIGH,
                "service_risk_cap_ratio": SERVICE_RISK_CAP_RATIO,
                "overstaffed_waste_trigger_ratio": OVERSTAFFED_WASTE_RATIO_TRIGGER,
            },
            "phase_metrics": risk_metrics,
            "impact_refresh": impact_refresh,
            "proven_gate": {
                "min_realized_samples": PROVEN_IMPACT_MIN_REALIZED_SAMPLES,
                "suppressed_count": len(suppressed_by_proven_gate),
                "suppressed_action_types": sorted(
                    list(
                        {
                            s.get("action_type")
                            for s in suppressed_by_proven_gate
                            if s.get("action_type")
                        }
                    )
                ),
                "suppressed_actions": suppressed_by_proven_gate,
            },
        },
        "actions": actions,
    }


def persist_next_actions(
    site_id: str, actions: list[dict], target_date: date | None = None
) -> dict:
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
        if action_key and recommendation_exists_for_action_key(
            site_id, action_type, action_key, target_date
        ):
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
