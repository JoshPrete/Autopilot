"""
Clubhouse Autopilot - Recursive Intelligence Engine

Runs a daily cycle: Measure → Learn → Observe → Analyze → Recommend.
Each cycle compounds — patterns with proven ROI get promoted,
patterns that don't work get suppressed.

Called after step_predict in the daily pipeline.
"""

import json
import logging
from datetime import date, datetime, time, timedelta
from typing import Optional

import anthropic

from config.database import engine
from config.settings import settings

logger = logging.getLogger("autopilot.intelligence")


# ============================================================
# Main Cycle
# ============================================================


def run_intelligence_cycle(
    site_id: str, site_name: str, cycle_date: date
) -> dict:
    """
    Full intelligence cycle. Called daily after step_predict.

    Returns summary of all phases.
    """
    logger.info("=== INTELLIGENCE CYCLE: %s (%s) ===", site_name, cycle_date)

    # Phase 1: MEASURE — Close the loop on past recommendations
    outcomes = measure_past_outcomes(site_id)
    logger.info("Phase 1 (Measure): %s", outcomes)

    # Phase 2: LEARN — Update patterns based on measured outcomes
    learning = update_patterns_from_outcomes(site_id, outcomes)
    logger.info("Phase 2 (Learn): %s", learning)

    # Phase 3: OBSERVE — Gather structured signals from all detectors
    signals = gather_signals(site_id, cycle_date)
    logger.info("Phase 3 (Observe): %d signals detected", len(signals))

    # Phase 4: ANALYZE — LLM synthesizes signals + learned patterns into insights
    insights = synthesize_insights(site_id, site_name, cycle_date, signals)
    logger.info("Phase 4 (Analyze): %d insights generated", len(insights))

    # Phase 5: RECOMMEND — Convert actionable insights to recommendations
    recs = create_intelligence_recommendations(site_id, cycle_date, insights)
    logger.info("Phase 5 (Recommend): %d recommendations created", len(recs))

    # Dispatch SMS digest if high-severity insights exist
    try:
        from delivery.sender import dispatch_intelligence_digest
        dispatch_intelligence_digest(site_id, insights)
    except Exception:
        logger.warning("Intelligence SMS digest failed (non-fatal)")

    result = {
        "outcomes_measured": outcomes.get("updated", 0),
        "patterns_strengthened": learning.get("strengthened", 0),
        "patterns_weakened": learning.get("weakened", 0),
        "patterns_suppressed": learning.get("suppressed", 0),
        "signals_count": len(signals),
        "insights_generated": len(insights),
        "recommendations_created": len(recs),
    }
    logger.info("Intelligence cycle complete: %s", result)
    return result


# ============================================================
# Phase 1: Measure
# ============================================================


def measure_past_outcomes(site_id: str) -> dict:
    """Compute realized impact for adopted recs that haven't been measured yet."""
    from data.storage import backfill_realized_impacts

    try:
        return backfill_realized_impacts(site_id, lookback_days=90)
    except Exception:
        logger.exception("measure_past_outcomes failed")
        return {"candidates": 0, "updated": 0}


# ============================================================
# Phase 2: Learn
# ============================================================


def update_patterns_from_outcomes(site_id: str, outcomes: dict) -> dict:
    """
    Update pattern confidence based on measured recommendation outcomes.

    Rules:
    - Positive outcome (profit delta > 0): confidence += 0.05, up to 0.95
    - Negative outcome (profit delta < 0): confidence -= 0.10, down to 0.05
    - If confidence < 0.15 after 3+ negative outcomes: suppress pattern
    """
    from data.storage import (
        get_learned_patterns,
        suppress_pattern,
        update_pattern_confidence,
    )

    if outcomes.get("updated", 0) == 0:
        return {"strengthened": 0, "weakened": 0, "suppressed": 0}

    # Get insights that have linked rec_ids with realized outcomes
    from sqlalchemy import text

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT
                    i.insight_type,
                    i.data->>'pattern_key' AS pattern_key,
                    r.outcome_data->'realized'->>'weekly_net_profit_delta_cents' AS profit_delta
                FROM insights i
                JOIN recommendations r ON r.rec_id = i.rec_id
                WHERE i.site_id = :sid
                  AND i.rec_id IS NOT NULL
                  AND r.outcome_data->'realized' IS NOT NULL
                  AND r.outcome_data->'realized'->>'weekly_net_profit_delta_cents' IS NOT NULL
                """
            ),
            {"sid": site_id},
        ).mappings().all()

    strengthened = 0
    weakened = 0
    suppressed_count = 0

    # Build lookup of current patterns
    all_patterns = get_learned_patterns(site_id, min_confidence=-1)
    pattern_lookup = {
        (p["pattern_type"], p["pattern_key"]): p for p in all_patterns
    }

    for row in rows:
        pattern_key = row.get("pattern_key")
        insight_type = row["insight_type"]
        if not pattern_key:
            continue

        key = (insight_type, pattern_key)
        pattern = pattern_lookup.get(key)
        if not pattern:
            continue

        try:
            profit_delta = int(float(row["profit_delta"]))
        except (ValueError, TypeError):
            continue

        current_conf = float(pattern["confidence"])

        if profit_delta > 0:
            new_conf = min(0.95, current_conf + 0.05)
            update_pattern_confidence(
                pattern["pattern_id"],
                new_conf,
                sample_size_delta=1,
                impact_cents_delta=profit_delta,
            )
            strengthened += 1
        elif profit_delta < 0:
            new_conf = max(0.05, current_conf - 0.10)
            update_pattern_confidence(
                pattern["pattern_id"],
                new_conf,
                sample_size_delta=1,
                impact_cents_delta=profit_delta,
            )
            weakened += 1

            # Suppress if consistently bad
            if new_conf < 0.15 and pattern["sample_size"] >= 3:
                suppress_pattern(pattern["pattern_id"])
                suppressed_count += 1

    return {
        "strengthened": strengthened,
        "weakened": weakened,
        "suppressed": suppressed_count,
    }


# ============================================================
# Phase 3: Observe — Signal Detectors
# ============================================================


def gather_signals(site_id: str, cycle_date: date) -> list[dict]:
    """Run all detectors and return combined signals, filtering suppressed patterns."""
    from data.storage import get_learned_patterns

    signals = []
    for detector in [
        detect_staffing_signals,
        detect_efficiency_gap_signals,
        detect_margin_signals,
        detect_demand_signals,
        detect_prediction_signals,
        detect_revenue_signals,
        detect_profitability_signals,
        detect_inventory_signals,
    ]:
        try:
            signals += detector(site_id, cycle_date)
        except Exception:
            logger.exception("Signal detector %s failed", detector.__name__)

    # Filter out signals matching suppressed patterns
    try:
        suppressed = get_learned_patterns(site_id, min_confidence=-1)
        suppressed_keys = {p["pattern_key"] for p in suppressed if p.get("suppressed")}
        if suppressed_keys:
            before = len(signals)
            signals = [s for s in signals if s.get("key") not in suppressed_keys]
            if before != len(signals):
                logger.info(
                    "Filtered %d suppressed signals", before - len(signals)
                )
    except Exception:
        pass

    return signals


def detect_staffing_signals(
    site_id: str, cycle_date: date, lookback_days: int = 28
) -> list[dict]:
    """
    Detect recurring staffing mismatches by (day_of_week, hour).

    Flags slots that are overstaffed or understaffed >= 3 of last 4 occurrences.
    """
    from data.storage import get_staffing_variance_intervals

    signals = []
    slot_tracker: dict[tuple, list] = {}  # (dow, hour) -> [status, ...]

    for offset in range(lookback_days):
        check_date = cycle_date - timedelta(days=offset)
        try:
            result = get_staffing_variance_intervals(site_id, check_date)
        except Exception:
            continue

        for interval in result.get("intervals", []):
            status = interval.get("status")
            if status not in ("understaffed", "overstaffed"):
                continue

            try:
                ts = datetime.fromisoformat(interval["interval_start"])
            except (ValueError, TypeError):
                continue

            dow = ts.weekday()
            hour = ts.hour
            slot = (dow, hour)

            if slot not in slot_tracker:
                slot_tracker[slot] = []
            slot_tracker[slot].append({
                "status": status,
                "staff_on": interval.get("staff_on", 0),
                "workload_per_staff": interval.get("workload_per_staff"),
                "date": check_date.isoformat(),
            })

    day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    for (dow, hour), entries in slot_tracker.items():
        # Only look at last 4 occurrences of this slot
        recent = entries[:4]
        if len(recent) < 3:
            continue

        overstaffed_count = sum(1 for e in recent if e["status"] == "overstaffed")
        understaffed_count = sum(1 for e in recent if e["status"] == "understaffed")

        if overstaffed_count >= 3:
            avg_staff = sum(e["staff_on"] for e in recent) / len(recent)
            signals.append({
                "signal_type": "staffing",
                "key": f"overstaffed_{day_names[dow].lower()}_{hour}",
                "title": f"{day_names[dow]} {hour}:00 consistently overstaffed",
                "evidence": {
                    "day": day_names[dow],
                    "hour": hour,
                    "occurrences": overstaffed_count,
                    "out_of": len(recent),
                    "avg_staff_on": round(avg_staff, 1),
                },
                "severity": "opportunity",
                "suggested_action": "STAFFING_ADJUST",
            })

        if understaffed_count >= 3:
            avg_wps = [e["workload_per_staff"] for e in recent if e.get("workload_per_staff")]
            signals.append({
                "signal_type": "staffing",
                "key": f"understaffed_{day_names[dow].lower()}_{hour}",
                "title": f"{day_names[dow]} {hour}:00 consistently understaffed",
                "evidence": {
                    "day": day_names[dow],
                    "hour": hour,
                    "occurrences": understaffed_count,
                    "out_of": len(recent),
                    "avg_workload_per_staff": round(sum(avg_wps) / len(avg_wps), 2) if avg_wps else None,
                },
                "severity": "warning",
                "suggested_action": "STAFFING_ADJUST",
            })

    return signals


def detect_efficiency_gap_signals(
    site_id: str, cycle_date: date, lookback_days: int = 28
) -> list[dict]:
    """
    Dollar-denominated staffing efficiency signals.

    Three signal types:
    1. Overall low efficiency (score < 0.80)
    2. Per-DOW recurring excess (> $13/day avg)
    3. Efficiency trend (comparing two halves of the window)
    """
    from config.constants import EFFICIENCY_SCORE_TARGET
    from data.storage import get_efficiency_gap_range

    signals = []
    start = cycle_date - timedelta(days=lookback_days)

    try:
        gap = get_efficiency_gap_range(site_id, start, cycle_date)
    except Exception:
        logger.exception("detect_efficiency_gap_signals: query failed")
        return signals

    totals = gap.get("totals", {})
    by_dow = gap.get("by_dow", [])
    by_day = gap.get("by_day", [])

    # Signal 1: Overall low efficiency
    eff_score = totals.get("efficiency_score", 1.0)
    excess = totals.get("excess_labor_cents", 0)
    days_analyzed = totals.get("days_analyzed", 0)

    if eff_score < 0.80 and days_analyzed >= 7:
        weekly_excess = round(excess / max(days_analyzed, 1) * 7)
        signals.append({
            "signal_type": "efficiency",
            "key": "overall_low_efficiency",
            "title": (
                f"Staffing efficiency at {round(eff_score * 100)}% "
                f"— ${weekly_excess / 100:,.0f}/week excess labor"
            ),
            "evidence": {
                "efficiency_score": round(eff_score, 4),
                "excess_labor_cents": excess,
                "weekly_excess_cents": weekly_excess,
                "days_analyzed": days_analyzed,
                "target": EFFICIENCY_SCORE_TARGET,
            },
            "severity": "warning",
            "suggested_action": "STAFFING_ADJUST",
        })

    # Signal 2: Per-DOW recurring excess (> $13/day = 1300 cents)
    for dow_entry in by_dow:
        avg_excess = dow_entry.get("avg_excess_labor_cents", 0)
        avg_eff = dow_entry.get("avg_efficiency_score", 1.0)
        samples = dow_entry.get("sample_days", 0)
        if avg_excess > 1300 and samples >= 2:
            signals.append({
                "signal_type": "efficiency",
                "key": f"dow_excess_{dow_entry['day_name'].lower()}",
                "title": (
                    f"{dow_entry['day_name']}: avg ${avg_excess / 100:,.0f}/day excess labor "
                    f"(efficiency: {round(avg_eff * 100)}%)"
                ),
                "evidence": {
                    "dow": dow_entry["dow"],
                    "day_name": dow_entry["day_name"],
                    "avg_excess_labor_cents": avg_excess,
                    "avg_efficiency_score": round(avg_eff, 4),
                    "sample_days": samples,
                },
                "severity": "opportunity",
                "suggested_action": "STAFFING_ADJUST",
            })

    # Signal 3: Efficiency trend (split window into halves)
    if len(by_day) >= 14:
        mid = len(by_day) // 2
        first_scores = [d["efficiency_score"] for d in by_day[:mid]]
        second_scores = [d["efficiency_score"] for d in by_day[mid:]]
        first_avg = sum(first_scores) / len(first_scores) if first_scores else 0
        second_avg = sum(second_scores) / len(second_scores) if second_scores else 0
        delta_pp = round((second_avg - first_avg) * 100, 1)

        if delta_pp < -5:
            signals.append({
                "signal_type": "efficiency",
                "key": "efficiency_trend_declining",
                "title": (
                    f"Staffing efficiency declined {abs(delta_pp):.0f}pp: "
                    f"{round(first_avg * 100)}% → {round(second_avg * 100)}%"
                ),
                "evidence": {
                    "first_half_avg": round(first_avg, 4),
                    "second_half_avg": round(second_avg, 4),
                    "delta_pp": delta_pp,
                    "window_days": len(by_day),
                },
                "severity": "warning",
                "suggested_action": "STAFFING_ADJUST",
            })

    return signals


def detect_margin_signals(
    site_id: str, cycle_date: date, lookback_days: int = 28
) -> list[dict]:
    """
    Detect margin concerns from item costs and profitability.

    Flags items with margin < 30% and cost increases > 10%.
    """
    from data.storage import get_daily_profitability, get_item_costs

    signals = []

    try:
        costs = get_item_costs(site_id)
    except Exception:
        return signals

    if not costs:
        return signals

    # Check item-level margins using profitability module
    try:
        from analysis.profitability import compute_item_margins

        margins = compute_item_margins(site_id, days=lookback_days)
        for m in margins:
            margin_pct = m.get("margin_pct", 100)
            if margin_pct < 30 and m.get("qty", 0) >= 10:
                signals.append({
                    "signal_type": "margin",
                    "key": f"low_margin_{m['item'].lower().replace(' ', '_')}",
                    "title": f"Low margin on {m['item']} ({margin_pct}%)",
                    "evidence": {
                        "item": m["item"],
                        "margin_pct": margin_pct,
                        "avg_price_cents": m.get("avg_price_cents"),
                        "cogs_cents": m.get("cogs_cents"),
                        "qty_sold": m.get("qty"),
                    },
                    "severity": "warning",
                    "suggested_action": "MARGIN_ALERT",
                })
    except Exception:
        pass

    # Check overall margin trends from daily_profitability
    try:
        start = cycle_date - timedelta(days=lookback_days)
        pnl = get_daily_profitability(site_id, start, cycle_date)
        if len(pnl) >= 14:
            # Split into two halves and compare
            mid = len(pnl) // 2
            first_half = pnl[:mid]
            second_half = pnl[mid:]

            first_labor_pct = [d["labor_pct"] for d in first_half if d.get("labor_pct")]
            second_labor_pct = [d["labor_pct"] for d in second_half if d.get("labor_pct")]

            if first_labor_pct and second_labor_pct:
                first_avg = sum(first_labor_pct) / len(first_labor_pct)
                second_avg = sum(second_labor_pct) / len(second_labor_pct)
                delta_pp = second_avg - first_avg

                if delta_pp > 3.0:
                    signals.append({
                        "signal_type": "margin",
                        "key": "labor_pct_trending_up",
                        "title": f"Labor % trending up (+{delta_pp:.1f}pp over {lookback_days} days)",
                        "evidence": {
                            "first_half_avg": round(first_avg, 1),
                            "second_half_avg": round(second_avg, 1),
                            "delta_pp": round(delta_pp, 1),
                        },
                        "severity": "warning",
                        "suggested_action": "REVENUE_INSIGHT",
                    })
    except Exception:
        pass

    return signals


def detect_demand_signals(
    site_id: str, cycle_date: date, lookback_days: int = 28
) -> list[dict]:
    """
    Detect demand shifts: items with > 20% volume change sustained 2+ weeks.
    """
    from sqlalchemy import text

    signals = []

    # Compare this-2-weeks vs previous-2-weeks for item volumes
    recent_end = cycle_date
    recent_start = cycle_date - timedelta(days=14)
    prev_end = recent_start
    prev_start = prev_end - timedelta(days=14)

    try:
        with engine.connect() as conn:
            recent_items = conn.execute(
                text(
                    "SELECT item_name, COUNT(*) AS cnt "
                    "FROM order_items "
                    "WHERE site_id = :sid AND created_at >= :s AND created_at < :e "
                    "GROUP BY item_name"
                ),
                {"sid": site_id, "s": recent_start, "e": recent_end},
            ).mappings().all()

            prev_items = conn.execute(
                text(
                    "SELECT item_name, COUNT(*) AS cnt "
                    "FROM order_items "
                    "WHERE site_id = :sid AND created_at >= :s AND created_at < :e "
                    "GROUP BY item_name"
                ),
                {"sid": site_id, "s": prev_start, "e": prev_end},
            ).mappings().all()

        recent_map = {r["item_name"]: int(r["cnt"]) for r in recent_items}
        prev_map = {r["item_name"]: int(r["cnt"]) for r in prev_items}

        for item in set(recent_map) | set(prev_map):
            recent_cnt = recent_map.get(item, 0)
            prev_cnt = prev_map.get(item, 0)

            if prev_cnt < 5:
                continue

            change_pct = ((recent_cnt - prev_cnt) / prev_cnt) * 100

            if abs(change_pct) >= 20:
                direction = "up" if change_pct > 0 else "down"
                signals.append({
                    "signal_type": "demand",
                    "key": f"demand_shift_{item.lower().replace(' ', '_')}_{direction}",
                    "title": f"{item} volume {direction} {abs(change_pct):.0f}% over 4 weeks",
                    "evidence": {
                        "item": item,
                        "recent_count": recent_cnt,
                        "prev_count": prev_cnt,
                        "change_pct": round(change_pct, 1),
                        "direction": direction,
                    },
                    "severity": "opportunity" if direction == "up" else "warning",
                    "suggested_action": "DEMAND_SHIFT",
                })

    except Exception:
        logger.exception("detect_demand_signals failed")

    return signals


def detect_prediction_signals(
    site_id: str, cycle_date: date, lookback_days: int = 28
) -> list[dict]:
    """
    Detect prediction accuracy issues: systematic bias or low accuracy streaks.
    """
    from analysis.accuracy import get_rolling_accuracy

    signals = []

    try:
        acc = get_rolling_accuracy(site_id, days_back=lookback_days, reference_date=cycle_date)

        # Check for alert from accuracy module
        if acc.get("alert"):
            signals.append({
                "signal_type": "prediction",
                "key": "prediction_accuracy_alert",
                "title": f"Prediction accuracy alert: {acc.get('alert_reason', 'low accuracy')}",
                "evidence": {
                    "avg_accuracy": acc.get("avg_accuracy"),
                    "days_measured": acc.get("days_measured"),
                    "trend": acc.get("trend"),
                },
                "severity": "warning",
                "suggested_action": "PREDICTION_DRIFT",
            })

        # Check for systematic bias by day-of-week
        daily = acc.get("daily_accuracies", [])
        if len(daily) >= 7:
            # Group by DOW
            dow_errors: dict[str, list] = {}
            for d in daily:
                try:
                    dt = date.fromisoformat(d["date"])
                    dow = dt.strftime("%A")
                    error = d.get("error_pct", 0)
                    if error is not None:
                        dow_errors.setdefault(dow, []).append(error)
                except Exception:
                    continue

            for dow, errors in dow_errors.items():
                if len(errors) >= 2:
                    mean_error = sum(errors) / len(errors)
                    if abs(mean_error) > 10:
                        direction = "over-predicting" if mean_error > 0 else "under-predicting"
                        signals.append({
                            "signal_type": "prediction",
                            "key": f"prediction_bias_{dow.lower()}",
                            "title": f"Systematic {direction} on {dow}s ({mean_error:+.1f}%)",
                            "evidence": {
                                "day": dow,
                                "mean_error_pct": round(mean_error, 1),
                                "sample_size": len(errors),
                            },
                            "severity": "info",
                            "suggested_action": "PREDICTION_DRIFT",
                        })

    except Exception:
        logger.exception("detect_prediction_signals failed")

    return signals


def detect_revenue_signals(
    site_id: str, cycle_date: date, lookback_days: int = 28
) -> list[dict]:
    """
    Detect revenue and labor efficiency trends from daily_profitability.
    """
    from data.storage import get_daily_profitability

    signals = []

    try:
        start = cycle_date - timedelta(days=lookback_days)
        pnl = get_daily_profitability(site_id, start, cycle_date)

        if len(pnl) < 14:
            return signals

        # Revenue per labor hour declining > 10%
        valid_rplh = [d for d in pnl if d.get("revenue_per_labor_hour") and d["revenue_per_labor_hour"] > 0]
        if len(valid_rplh) >= 14:
            mid = len(valid_rplh) // 2
            first_avg = sum(d["revenue_per_labor_hour"] for d in valid_rplh[:mid]) / mid
            second_avg = sum(d["revenue_per_labor_hour"] for d in valid_rplh[mid:]) / (len(valid_rplh) - mid)

            if first_avg > 0:
                change_pct = ((second_avg - first_avg) / first_avg) * 100
                if change_pct < -10:
                    signals.append({
                        "signal_type": "revenue",
                        "key": "rev_per_labor_hour_declining",
                        "title": f"Revenue per labor hour declining ({change_pct:.1f}%)",
                        "evidence": {
                            "first_half_avg_cents": round(first_avg),
                            "second_half_avg_cents": round(second_avg),
                            "change_pct": round(change_pct, 1),
                        },
                        "severity": "warning",
                        "suggested_action": "REVENUE_INSIGHT",
                    })

        # Most/least profitable day of week
        dow_profit: dict[str, list] = {}
        for d in pnl:
            if d.get("net_profit_cents") is not None:
                try:
                    dt = date.fromisoformat(d["date"])
                    dow = dt.strftime("%A")
                    dow_profit.setdefault(dow, []).append(d["net_profit_cents"])
                except Exception:
                    continue

        if len(dow_profit) >= 5:
            dow_avgs = {
                dow: sum(vals) / len(vals)
                for dow, vals in dow_profit.items()
                if len(vals) >= 2
            }
            if dow_avgs:
                best_dow = max(dow_avgs, key=dow_avgs.get)
                worst_dow = min(dow_avgs, key=dow_avgs.get)

                if dow_avgs[best_dow] > 0:
                    signals.append({
                        "signal_type": "revenue",
                        "key": f"best_day_{best_dow.lower()}",
                        "title": f"{best_dow} is consistently the most profitable day",
                        "evidence": {
                            "day": best_dow,
                            "avg_net_profit_cents": round(dow_avgs[best_dow]),
                            "sample_size": len(dow_profit[best_dow]),
                        },
                        "severity": "info",
                        "suggested_action": None,
                    })

                if dow_avgs[worst_dow] < dow_avgs[best_dow] * 0.5:
                    signals.append({
                        "signal_type": "revenue",
                        "key": f"worst_day_{worst_dow.lower()}",
                        "title": f"{worst_dow} is consistently the least profitable day",
                        "evidence": {
                            "day": worst_dow,
                            "avg_net_profit_cents": round(dow_avgs[worst_dow]),
                            "vs_best": round(dow_avgs[best_dow]),
                        },
                        "severity": "opportunity",
                        "suggested_action": "REVENUE_INSIGHT",
                    })

    except Exception:
        logger.exception("detect_revenue_signals failed")

    return signals


def detect_profitability_signals(
    site_id: str, cycle_date: date, lookback_days: int = 28
) -> list[dict]:
    """
    Three-way cross-correlation detector: Xero COGS × Square revenue × Deputy labor.

    Six analyses:
    A. Per-item unit economics (labor-adjusted margin)
    B. Optimal staffing by DOW (profit-based)
    C. Revenue per labor dollar trends
    D. COGS-to-revenue ratio trends
    E. High-value understaffed windows
    F. Menu mix profit optimization
    """
    from data.storage import (
        get_daily_efficiency_snapshot,
        get_daily_profitability,
        get_item_costs,
        get_profitability_correlations,
        has_real_cogs,
    )
    from sqlalchemy import text

    signals = []

    if not has_real_cogs(site_id):
        return signals

    start = cycle_date - timedelta(days=lookback_days)

    # --- A. Per-Item Unit Economics (Revenue × COGS × Labor) ---
    try:
        from analysis.profitability import compute_item_margins

        margins = compute_item_margins(site_id, days=lookback_days)
        pnl = get_daily_profitability(site_id, start, cycle_date)

        if margins and pnl:
            # Estimate daily labor allocation per item based on workload share
            total_labor = sum(d["labor_cost_cents"] for d in pnl if d.get("labor_cost_cents"))
            total_items = sum(d.get("item_count", 0) or 0 for d in pnl)
            labor_per_item = total_labor / total_items if total_items > 0 else 0

            for m in margins:
                if m.get("qty", 0) < 50:
                    continue
                avg_price = m.get("avg_price_cents", 0)
                cogs = m.get("cogs_cents", 0)
                gross_margin_pct = m.get("margin_pct", 100)

                # Approximate labor cost per item
                item_labor = round(labor_per_item)
                labor_adjusted_profit = avg_price - cogs - item_labor
                labor_adjusted_margin = round(
                    (labor_adjusted_profit / avg_price) * 100, 1
                ) if avg_price > 0 else 0

                if labor_adjusted_margin < 15 and gross_margin_pct >= 25:
                    item_key = m["item"].lower().replace(" ", "_")
                    signals.append({
                        "signal_type": "profitability",
                        "key": f"low_profit_after_labor_{item_key}",
                        "title": f"{m['item']} has {gross_margin_pct}% gross margin but only {labor_adjusted_margin}% after labor",
                        "evidence": {
                            "item": m["item"],
                            "qty": m["qty"],
                            "avg_price_cents": avg_price,
                            "cogs_cents": cogs,
                            "labor_per_item_cents": item_labor,
                            "gross_margin_pct": gross_margin_pct,
                            "labor_adjusted_margin_pct": labor_adjusted_margin,
                        },
                        "severity": "warning",
                        "suggested_action": "MARGIN_ALERT",
                    })
    except Exception:
        logger.exception("Profitability signal A (unit economics) failed")

    # --- B. Optimal Staffing by DOW (Profit-Based) ---
    try:
        correlations = get_profitability_correlations(site_id, days=lookback_days)
        optimal = correlations.get("optimal_staffing", [])
        by_dow = {d["dow"]: d for d in correlations.get("by_dow", [])}

        day_names = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

        for opt in optimal:
            dow_data = by_dow.get(opt["dow"])
            if not dow_data:
                continue
            current_staff = dow_data.get("avg_staff_count", 0)
            optimal_staff = opt["optimal_staff"]
            if current_staff > 0 and abs(current_staff - optimal_staff) >= 1:
                current_pps = round(dow_data["avg_net_profit_cents"] / current_staff) if current_staff > 0 else 0
                optimal_pps = opt["profit_per_staff"]
                day_name = day_names[opt["dow"]] if opt["dow"] < 7 else opt.get("day_name", "?")
                direction = "fewer" if optimal_staff < current_staff else "more"
                signals.append({
                    "signal_type": "profitability",
                    "key": f"optimal_staff_{day_name.lower()}",
                    "title": f"{day_name}: historically {optimal_staff} staff yielded ${optimal_pps / 100:.0f}/staff. Currently avg {current_staff:.0f} → ${current_pps / 100:.0f}/staff. Worth trying {direction}?",
                    "evidence": {
                        "dow": opt["dow"],
                        "day": day_name,
                        "best_observed_staff": optimal_staff,
                        "current_avg_staff": round(current_staff, 1),
                        "profit_per_staff_at_best": optimal_pps,
                        "profit_per_staff_current": current_pps,
                        "note": "Historical correlation, not causal — experiment recommended",
                    },
                    "severity": "opportunity",
                    "suggested_action": "STAFFING_ADJUST",
                })
    except Exception:
        logger.exception("Profitability signal B (optimal staffing) failed")

    # --- C. Revenue per Labor Dollar Trends ---
    try:
        pnl = get_daily_profitability(site_id, start, cycle_date)
        valid = [d for d in pnl if d.get("revenue_cents") and d.get("labor_cost_cents") and d["labor_cost_cents"] > 0]

        if len(valid) >= 14:
            mid = len(valid) // 2
            first_ratios = [d["revenue_cents"] / d["labor_cost_cents"] for d in valid[:mid]]
            second_ratios = [d["revenue_cents"] / d["labor_cost_cents"] for d in valid[mid:]]

            first_avg = sum(first_ratios) / len(first_ratios)
            second_avg = sum(second_ratios) / len(second_ratios)

            if first_avg > 0:
                change_pct = ((second_avg - first_avg) / first_avg) * 100
                if change_pct < -10:
                    signals.append({
                        "signal_type": "profitability",
                        "key": "rev_per_labor_dollar_declining",
                        "title": f"Revenue per labor dollar declining ({change_pct:.1f}% over {lookback_days} days)",
                        "evidence": {
                            "first_half_avg": round(first_avg, 2),
                            "second_half_avg": round(second_avg, 2),
                            "change_pct": round(change_pct, 1),
                        },
                        "severity": "warning",
                        "suggested_action": "REVENUE_INSIGHT",
                    })

            # Check specific DOW underperformers
            dow_ratios: dict[str, list] = {}
            for d in valid:
                try:
                    dt = date.fromisoformat(d["date"])
                    dow = dt.strftime("%A")
                    ratio = d["revenue_cents"] / d["labor_cost_cents"]
                    dow_ratios.setdefault(dow, []).append(ratio)
                except Exception:
                    continue

            if dow_ratios:
                overall_avg = sum(sum(v) for v in dow_ratios.values()) / sum(len(v) for v in dow_ratios.values())
                for dow, ratios in dow_ratios.items():
                    if len(ratios) >= 2:
                        dow_avg = sum(ratios) / len(ratios)
                        if dow_avg < overall_avg * 0.75:
                            signals.append({
                                "signal_type": "profitability",
                                "key": f"low_labor_roi_{dow.lower()}",
                                "title": f"{dow} has low revenue per labor dollar (${dow_avg:.2f} vs ${overall_avg:.2f} avg)",
                                "evidence": {
                                    "day": dow,
                                    "dow_avg_ratio": round(dow_avg, 2),
                                    "overall_avg_ratio": round(overall_avg, 2),
                                    "sample_size": len(ratios),
                                },
                                "severity": "opportunity",
                                "suggested_action": "REVENUE_INSIGHT",
                            })
    except Exception:
        logger.exception("Profitability signal C (rev/labor dollar) failed")

    # --- D. COGS-to-Revenue Ratio Trends ---
    try:
        pnl = get_daily_profitability(site_id, start, cycle_date)
        valid = [d for d in pnl if d.get("cogs_cents") and d.get("revenue_cents") and d["revenue_cents"] > 0]

        if len(valid) >= 14:
            mid = len(valid) // 2
            first_cogs_pct = [d["cogs_cents"] / d["revenue_cents"] * 100 for d in valid[:mid]]
            second_cogs_pct = [d["cogs_cents"] / d["revenue_cents"] * 100 for d in valid[mid:]]

            first_avg = sum(first_cogs_pct) / len(first_cogs_pct)
            second_avg = sum(second_cogs_pct) / len(second_cogs_pct)
            delta_pp = second_avg - first_avg

            # Also check revenue trend to detect margin compression
            first_rev = sum(d["revenue_cents"] for d in valid[:mid]) / len(valid[:mid])
            second_rev = sum(d["revenue_cents"] for d in valid[mid:]) / len(valid[mid:])
            rev_change_pct = ((second_rev - first_rev) / first_rev) * 100 if first_rev > 0 else 0

            if delta_pp > 2.0 and abs(rev_change_pct) < 10:
                signals.append({
                    "signal_type": "profitability",
                    "key": "cogs_ratio_rising",
                    "title": f"COGS ratio rising +{delta_pp:.1f}pp while revenue flat — margin compression",
                    "evidence": {
                        "first_half_cogs_pct": round(first_avg, 1),
                        "second_half_cogs_pct": round(second_avg, 1),
                        "delta_pp": round(delta_pp, 1),
                        "revenue_change_pct": round(rev_change_pct, 1),
                    },
                    "severity": "warning",
                    "suggested_action": "MARGIN_ALERT",
                })
    except Exception:
        logger.exception("Profitability signal D (COGS ratio) failed")

    # --- E. High-Value Understaffed Windows ---
    try:
        day_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        # Aggregate across recent days
        window_tracker: dict[tuple, list] = {}  # (dow, hour) -> [revenue_cents, ...]

        for offset in range(min(lookback_days, 14)):
            check_date = cycle_date - timedelta(days=offset)
            try:
                snap = get_daily_efficiency_snapshot(site_id, check_date)
            except Exception:
                continue

            for interval in snap.get("intervals", []):
                if interval.get("status") != "understaffed":
                    continue
                rev = interval.get("revenue_cents", 0)
                if rev <= 0:
                    continue
                try:
                    ts = datetime.fromisoformat(interval["interval_start"])
                except (ValueError, TypeError):
                    continue
                dow = ts.weekday()
                hour = ts.hour
                window_tracker.setdefault((dow, hour), []).append(rev)

        for (dow, hour), revenues in window_tracker.items():
            if len(revenues) < 2:
                continue
            avg_rev = sum(revenues) / len(revenues)
            if avg_rev > 5000:  # > $50 per 15-min interval while understaffed
                signals.append({
                    "signal_type": "profitability",
                    "key": f"high_revenue_understaffed_{day_names[dow].lower()}_{hour}",
                    "title": f"{day_names[dow]} {hour}:00 consistently understaffed with ${avg_rev / 100:.0f} avg revenue",
                    "evidence": {
                        "day": day_names[dow],
                        "hour": hour,
                        "occurrences": len(revenues),
                        "avg_revenue_cents": round(avg_rev),
                    },
                    "severity": "opportunity",
                    "suggested_action": "STAFFING_ADJUST",
                })
    except Exception:
        logger.exception("Profitability signal E (understaffed windows) failed")

    # --- F. Menu Mix Profit Optimization ---
    try:
        from analysis.profitability import compute_item_margins

        margins = compute_item_margins(site_id, days=lookback_days)
        if margins and len(margins) >= 5:
            total_qty = sum(m.get("qty", 0) for m in margins)
            if total_qty > 0:
                # Enrich with volume share
                for m in margins:
                    m["volume_pct"] = round(m.get("qty", 0) / total_qty * 100, 1)
                    m["total_profit_cents"] = m.get("total_profit_cents", 0)

                by_volume = sorted(margins, key=lambda x: x.get("qty", 0), reverse=True)[:10]
                by_profit = sorted(margins, key=lambda x: x.get("total_profit_cents", 0), reverse=True)[:10]

                # Find high-volume items with below-median margins
                margin_values = [m.get("margin_pct", 0) for m in margins if m.get("qty", 0) >= 5]
                if margin_values:
                    median_margin = sorted(margin_values)[len(margin_values) // 2]

                    top_seller = by_volume[0] if by_volume else None
                    top_profit = by_profit[0] if by_profit else None

                    if (top_seller and top_profit
                            and top_seller["item"] != top_profit["item"]
                            and top_seller.get("margin_pct", 100) < median_margin):
                        signals.append({
                            "signal_type": "profitability",
                            "key": "menu_mix_opportunity",
                            "title": (
                                f"Top seller ({top_seller['item']}, {top_seller['volume_pct']}% vol) "
                                f"has {top_seller.get('margin_pct', 0)}% margin. "
                                f"{top_profit['item']} ({top_profit['volume_pct']}% vol) "
                                f"has {top_profit.get('margin_pct', 0)}% margin"
                            ),
                            "evidence": {
                                "top_seller": top_seller["item"],
                                "top_seller_volume_pct": top_seller["volume_pct"],
                                "top_seller_margin_pct": top_seller.get("margin_pct"),
                                "top_profit_item": top_profit["item"],
                                "top_profit_volume_pct": top_profit["volume_pct"],
                                "top_profit_margin_pct": top_profit.get("margin_pct"),
                                "median_margin_pct": median_margin,
                            },
                            "severity": "opportunity",
                            "suggested_action": "MARGIN_ALERT",
                        })
    except Exception:
        logger.exception("Profitability signal F (menu mix) failed")

    return signals


def detect_inventory_signals(
    site_id: str, cycle_date: date, lookback_days: int = 21
) -> list[dict]:
    """
    Detect operational stock risks using:
      effective_on_hand = latest_count + receipts - consumed_sales

    Produces actionable restock signals for low/out-of-stock and reorder-soon states.
    """
    from data.storage import get_inventory_alerts

    signals = []
    try:
        alerts = get_inventory_alerts(site_id, lookback_days=lookback_days, include_ok=False)
    except Exception:
        logger.exception("detect_inventory_signals failed")
        return signals

    for alert in alerts[:20]:
        status = alert.get("status")
        item_name = alert.get("item_name", "Inventory item")
        key_base = (
            str(alert.get("score_key") or item_name)
            .lower()
            .replace(" ", "_")
            .replace("-", "_")
        )
        on_hand = alert.get("effective_on_hand")
        reorder_point = alert.get("reorder_point")
        days_remaining = alert.get("days_remaining")
        reorder_units = alert.get("recommended_reorder_units")

        if status in ("out_of_stock", "low_stock"):
            severity = "warning"
            title = (
                f"{item_name} out of stock"
                if status == "out_of_stock"
                else (
                    f"{item_name} low stock "
                    f"({round(float(on_hand or 0), 1)} remaining)"
                )
            )
        elif status == "reorder_soon":
            severity = "opportunity"
            days_text = (
                f"{round(float(days_remaining), 1)} days remaining"
                if days_remaining is not None
                else "reorder soon"
            )
            title = f"{item_name} nearing reorder point ({days_text})"
        elif status == "needs_count":
            severity = "warning"
            title = f"{item_name} needs a physical stock count"
        else:
            continue

        signals.append(
            {
                "signal_type": "operations",
                "key": f"inventory_{status}_{key_base}",
                "title": title,
                "evidence": {
                    "item_name": item_name,
                    "status": status,
                    "effective_on_hand": on_hand,
                    "reorder_point": reorder_point,
                    "recommended_reorder_units": reorder_units,
                    "days_remaining": days_remaining,
                    "window_days": alert.get("window_days"),
                },
                "severity": severity,
                "suggested_action": "INVENTORY_RESTOCK",
            }
        )

    return signals


# ============================================================
# Phase 4: Synthesize — LLM Analysis
# ============================================================


def synthesize_insights(
    site_id: str,
    site_name: str,
    cycle_date: date,
    signals: list[dict],
) -> list[dict]:
    """
    Single Claude API call. Takes structured signals + learned patterns,
    returns prioritized insights with natural language titles/descriptions.
    """
    from data.storage import get_learned_patterns, get_recent_insights, store_insight, store_learned_pattern

    if not signals:
        logger.info("No signals to synthesize")
        return []

    if not settings.ANTHROPIC_API_KEY:
        logger.warning("ANTHROPIC_API_KEY not configured, skipping synthesis")
        return _fallback_insights(site_id, cycle_date, signals)

    # Get context for LLM
    patterns = get_learned_patterns(site_id, min_confidence=0.3)
    recent = get_recent_insights(site_id, days=7)
    recent_titles = [i["title"] for i in recent]

    # Build prompt
    system_prompt = (
        f"You are analyzing operations for {site_name}, a specialty coffee cafe. "
        "You receive structured signals detected by automated analysis and must "
        "synthesize them into prioritized business insights.\n\n"
        "Return a JSON array of the top 5 insights (or fewer if not enough signals). "
        "Each insight must have:\n"
        '- "insight_type": one of "staffing", "margin", "demand", "prediction", "revenue", "operations"\n'
        '- "severity": one of "info", "warning", "opportunity"\n'
        '- "title": concise title (under 80 chars)\n'
        '- "body": 1-3 sentence explanation with specific numbers\n'
        '- "action_type": one of "STAFFING_ADJUST", "MARGIN_ALERT", "DEMAND_SHIFT", '
        '"PREDICTION_DRIFT", "REVENUE_INSIGHT", "INVENTORY_RESTOCK" or null if info-only\n'
        '- "confidence": 0.0-1.0 based on evidence strength\n'
        '- "pattern_key": unique identifier like "overstaffed_tuesday_14"\n\n'
        "Rules:\n"
        "- Only include insights that are NEW or materially changed vs recent insights\n"
        "- Prioritize: opportunities > warnings > info\n"
        "- Be specific with numbers — don't say 'significantly', say '23% increase'\n"
        "- Reference specific days, times, or items\n"
        "- Return ONLY the JSON array, no other text"
    )

    user_content = json.dumps({
        "signals_detected_today": signals[:20],  # Limit to avoid token overflow
        "previously_learned_patterns": [
            {"key": p["pattern_key"], "description": p["description"],
             "confidence": float(p["confidence"])}
            for p in patterns[:10]
        ],
        "recent_insight_titles_avoid_repeating": recent_titles[:10],
    }, default=str)

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=2000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_content}],
        )

        # Parse response
        response_text = response.content[0].text.strip()
        # Handle potential markdown code blocks
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[1]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

        raw_insights = json.loads(response_text)
        if not isinstance(raw_insights, list):
            logger.warning("LLM returned non-list: %s", type(raw_insights))
            return _fallback_insights(site_id, cycle_date, signals)

    except (json.JSONDecodeError, anthropic.APIError) as e:
        logger.warning("LLM synthesis failed: %s — falling back to rule-based", e)
        return _fallback_insights(site_id, cycle_date, signals)

    # Store insights and patterns
    stored_insights = []
    for raw in raw_insights[:5]:
        insight_type = raw.get("insight_type", "operations")
        title = raw.get("title", "Untitled insight")
        body = raw.get("body", "")
        severity = raw.get("severity", "info")
        action_type = raw.get("action_type")
        confidence = float(raw.get("confidence", 0.5))
        pattern_key = raw.get("pattern_key", "")

        insight_id = store_insight(
            site_id=site_id,
            cycle_date=cycle_date,
            insight_type=insight_type,
            severity=severity,
            title=title,
            body=body,
            data={"pattern_key": pattern_key, "source": "llm"},
            action_type=action_type,
            confidence=confidence,
            expires_at=cycle_date + timedelta(days=14),
        )

        if insight_id:
            stored_insights.append({
                "insight_id": insight_id,
                "insight_type": insight_type,
                "severity": severity,
                "title": title,
                "body": body,
                "action_type": action_type,
                "confidence": confidence,
                "pattern_key": pattern_key,
            })

        # Store/update learned pattern
        if pattern_key:
            try:
                store_learned_pattern(
                    site_id=site_id,
                    pattern_type=insight_type,
                    pattern_key=pattern_key,
                    description=title,
                    pattern_data={"latest_body": body, "last_cycle": cycle_date.isoformat()},
                    confidence=confidence,
                )
            except Exception:
                logger.warning("Failed to store pattern %s", pattern_key)

    return stored_insights


def _fallback_insights(
    site_id: str, cycle_date: date, signals: list[dict]
) -> list[dict]:
    """
    Rule-based fallback when LLM is unavailable.
    Converts top signals directly into insights without LLM synthesis.
    """
    from data.storage import store_insight, store_learned_pattern

    # Sort by severity priority
    severity_order = {"warning": 0, "opportunity": 1, "info": 2}
    sorted_signals = sorted(signals, key=lambda s: severity_order.get(s.get("severity", "info"), 2))

    stored = []
    for signal in sorted_signals[:5]:
        insight_id = store_insight(
            site_id=site_id,
            cycle_date=cycle_date,
            insight_type=signal.get("signal_type", "operations"),
            severity=signal.get("severity", "info"),
            title=signal.get("title", "Signal detected"),
            body=json.dumps(signal.get("evidence", {})),
            data={"pattern_key": signal.get("key", ""), "source": "rule_based"},
            action_type=signal.get("suggested_action"),
            confidence=0.5,
            expires_at=cycle_date + timedelta(days=14),
        )

        if insight_id:
            stored.append({
                "insight_id": insight_id,
                "insight_type": signal.get("signal_type", "operations"),
                "severity": signal.get("severity", "info"),
                "title": signal.get("title", "Signal detected"),
                "body": json.dumps(signal.get("evidence", {})),
                "action_type": signal.get("suggested_action"),
                "confidence": 0.5,
                "pattern_key": signal.get("key", ""),
            })

        # Store pattern
        key = signal.get("key", "")
        if key:
            try:
                store_learned_pattern(
                    site_id=site_id,
                    pattern_type=signal.get("signal_type", "operations"),
                    pattern_key=key,
                    description=signal.get("title", key),
                    pattern_data=signal.get("evidence", {}),
                    confidence=0.5,
                )
            except Exception:
                pass

    return stored


# ============================================================
# Phase 5: Recommend
# ============================================================


def create_intelligence_recommendations(
    site_id: str,
    cycle_date: date,
    insights: list[dict],
) -> list[str]:
    """
    Convert actionable insights into recommendations table entries.
    Only creates recs for severity='opportunity' or 'warning' insights.
    """
    from data.storage import (
        recommendation_exists_for_action_key,
        store_recommendation,
        update_insight_status,
    )

    created = []
    for insight in insights:
        if insight.get("severity") == "info":
            continue
        if not insight.get("action_type"):
            continue

        action_key = f"intel_{insight['insight_type']}_{insight.get('pattern_key', '')}"

        if recommendation_exists_for_action_key(
            site_id, insight["action_type"], action_key, cycle_date
        ):
            continue

        try:
            rec_id = store_recommendation(
                prediction_id=None,
                site_id=site_id,
                action_type=insight["action_type"],
                action_timing=datetime.combine(cycle_date, time(8, 0)),
                owner_role="MANAGER",
                action_details={
                    "action_key": action_key,
                    "source": "intelligence_engine",
                    "insight_id": str(insight.get("insight_id", "")),
                    "insight_type": insight["insight_type"],
                    "title": insight["title"],
                    "message": insight.get("body", ""),
                    "confidence": insight.get("confidence", 0.5),
                },
            )

            # Link rec back to insight
            if insight.get("insight_id"):
                update_insight_status(insight["insight_id"], rec_id=rec_id)

            created.append(rec_id)
        except Exception:
            logger.exception("Failed to create recommendation for insight: %s", insight.get("title"))

    return created
