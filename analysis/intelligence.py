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
        detect_margin_signals,
        detect_demand_signals,
        detect_prediction_signals,
        detect_revenue_signals,
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
        '"PREDICTION_DRIFT", "REVENUE_INSIGHT" or null if info-only\n'
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
