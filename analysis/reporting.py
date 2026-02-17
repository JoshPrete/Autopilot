"""
Clubhouse Autopilot v1.2 - Weekly Reporting
Performance metrics and ops review (Spec Section 1.2, 13.2)

Generates the Weekly Ops Review sent Monday 8am. Combines
prediction accuracy, adoption rates, labour metrics, and
actionable insights into a single report.

Core Outputs (Section 1.2):
    3. Weekly Ops Review - Performance metrics (Monday 8am)
"""

import json
import logging
from datetime import date, timedelta
from typing import Optional

from config.database import engine
from analysis.accuracy import (
    get_adoption_metrics,
    get_rolling_accuracy,
)
from data.storage import get_daily_profitability, get_weekly_stats

logger = logging.getLogger("autopilot.reporting")

# Report width for text formatting
REPORT_WIDTH = 59


# ============================================================
# Weekly Review Report
# ============================================================


def generate_weekly_review(
    site_id: str,
    site_name: str,
    week_end: date = None,
) -> dict:
    """
    Generate the complete weekly ops review.

    Called by the Monday 8am cron job. Covers the previous
    Monday-Sunday period.

    Returns:
        Dict with report text, metrics, and insights
    """
    if week_end is None:
        # Default to last Sunday
        today = date.today()
        days_since_sunday = (today.weekday() + 1) % 7
        week_end = today - timedelta(days=days_since_sunday)

    week_start = week_end - timedelta(days=6)

    # Gather metrics
    accuracy = get_rolling_accuracy(site_id, days_back=7, reference_date=week_end)
    adoption = get_adoption_metrics(site_id, week_start, week_end)
    stats = get_weekly_stats(site_id, week_start, week_end)

    # Get daily prediction details
    daily_details = _get_daily_details(site_id, week_start, week_end)

    # Generate insights
    insights = _generate_insights(accuracy, adoption, daily_details)

    # Build the text report
    report_text = _format_weekly_report(
        site_name=site_name,
        week_start=week_start,
        week_end=week_end,
        accuracy=accuracy,
        adoption=adoption,
        stats=stats,
        daily_details=daily_details,
        insights=insights,
    )

    # Build SMS summary
    sms_text = _format_weekly_sms(
        site_name=site_name,
        week_start=week_start,
        week_end=week_end,
        accuracy=accuracy,
        adoption=adoption,
    )

    # Store the review
    review_id = _store_review(
        site_id=site_id,
        week_start=week_start,
        week_end=week_end,
        accuracy=accuracy,
        adoption=adoption,
        insights=insights,
    )

    return {
        "review_id": review_id,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "report_text": report_text,
        "sms_summary": sms_text,
        "accuracy": accuracy,
        "adoption": adoption,
        "insights": insights,
        "daily_details": daily_details,
    }


# ============================================================
# Weekly ROI Report
# ============================================================


def generate_weekly_roi_report(
    site_id: str,
    site_name: str,
    week_end: date = None,
) -> dict:
    """
    Generate a weekly ROI summary from daily_profitability.

    Focus metrics:
      - labour % (average)
      - revenue per labour hour
      - net profit trend (week-over-week)
    """
    if week_end is None:
        today = date.today()
        days_since_sunday = (today.weekday() + 1) % 7
        week_end = today - timedelta(days=days_since_sunday)

    week_start = week_end - timedelta(days=6)
    prev_week_start = week_start - timedelta(days=7)
    prev_week_end = week_end - timedelta(days=7)

    current_rows = get_daily_profitability(site_id, week_start, week_end)
    previous_rows = get_daily_profitability(site_id, prev_week_start, prev_week_end)

    current = _aggregate_profitability_rows(current_rows)
    previous = _aggregate_profitability_rows(previous_rows)

    deltas = {
        "revenue_cents_delta": (
            current["total_revenue_cents"] - previous["total_revenue_cents"]
            if previous["days"] > 0 else None
        ),
        "net_profit_cents_delta": (
            current["total_net_profit_cents"] - previous["total_net_profit_cents"]
            if previous["days"] > 0 else None
        ),
        "labor_pct_delta_pp": _pp_change(
            current["avg_labor_pct"],
            previous["avg_labor_pct"],
        ),
        "revenue_per_labor_hour_delta_pct": _pct_change(
            current["avg_revenue_per_labor_hour_cents"],
            previous["avg_revenue_per_labor_hour_cents"],
        ),
    }

    headline = "Insufficient profitability data."
    if current["days"] > 0:
        net_delta = deltas["net_profit_cents_delta"]
        if net_delta is None:
            headline = "Current week profitability baseline generated."
        elif net_delta >= 0:
            headline = (
                f"Net profit improved by ${net_delta / 100:,.0f} week-over-week."
            )
        else:
            headline = (
                f"Net profit down by ${abs(net_delta) / 100:,.0f} week-over-week."
            )

    report_text = _format_weekly_roi_report(
        site_name=site_name,
        week_start=week_start,
        week_end=week_end,
        current=current,
        previous=previous,
        deltas=deltas,
        headline=headline,
    )

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "previous_week_start": prev_week_start.isoformat(),
        "previous_week_end": prev_week_end.isoformat(),
        "headline": headline,
        "current_week": current,
        "previous_week": previous,
        "deltas": deltas,
        "report_text": report_text,
    }


# ============================================================
# Daily Details
# ============================================================


def _get_daily_details(
    site_id: str,
    week_start: date,
    week_end: date,
) -> list[dict]:
    """Get prediction vs actual for each day of the week."""
    from sqlalchemy import text

    with engine.connect() as conn:
        result = conn.execute(
            text(
                "SELECT forecast_date, forecast_data, confidence_score, "
                "actual_accuracy "
                "FROM predictions "
                "WHERE site_id = :sid "
                "AND forecast_date BETWEEN :start AND :end "
                "ORDER BY forecast_date"
            ),
            {"sid": site_id, "start": week_start, "end": week_end},
        )
        rows = list(result.mappings())

    details = []
    for row in rows:
        forecast_data = row["forecast_data"]
        if isinstance(forecast_data, str):
            forecast_data = json.loads(forecast_data)

        details.append({
            "date": str(row["forecast_date"]),
            "day_name": date.fromisoformat(str(row["forecast_date"])).strftime("%A"),
            "predicted_drinks": forecast_data.get("total_predicted_drinks", 0),
            "rush_count": forecast_data.get("rush_count", 0),
            "confidence": float(row["confidence_score"]) if row["confidence_score"] else None,
            "accuracy": round(float(row["actual_accuracy"]) * 100, 1) if row["actual_accuracy"] else None,
        })

    return details


def _aggregate_profitability_rows(rows: list[dict]) -> dict:
    """Aggregate daily profitability rows into weekly metrics."""
    days = len(rows)

    total_revenue = sum(r.get("revenue_cents", 0) or 0 for r in rows)
    total_labor = sum(r.get("labor_cost_cents", 0) or 0 for r in rows)
    total_cogs = sum(r.get("cogs_cents", 0) or 0 for r in rows)
    total_net = sum(r.get("net_profit_cents", 0) or 0 for r in rows)

    labor_pct_values = [r["labor_pct"] for r in rows if r.get("labor_pct") is not None]
    rev_per_hour_values = [
        r["revenue_per_labor_hour"]
        for r in rows
        if r.get("revenue_per_labor_hour") is not None
    ]

    avg_labor_pct = (
        round(sum(labor_pct_values) / len(labor_pct_values), 2)
        if labor_pct_values else None
    )
    avg_rev_per_hour = (
        int(round(sum(rev_per_hour_values) / len(rev_per_hour_values)))
        if rev_per_hour_values else None
    )

    return {
        "days": days,
        "total_revenue_cents": total_revenue,
        "total_labor_cost_cents": total_labor,
        "total_cogs_cents": total_cogs,
        "total_net_profit_cents": total_net,
        "avg_labor_pct": avg_labor_pct,
        "avg_revenue_per_labor_hour_cents": avg_rev_per_hour,
    }


def _pct_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous in (None, 0):
        return None
    return round(((current - previous) / previous) * 100, 2)


def _pp_change(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    if current is None or previous is None:
        return None
    return round(current - previous, 2)


# ============================================================
# Insight Generation
# ============================================================


def _generate_insights(
    accuracy: dict,
    adoption: dict,
    daily_details: list[dict],
) -> list[str]:
    """Generate actionable insights from the week's data."""
    insights = []

    # Accuracy insights
    avg_acc = accuracy.get("avg_accuracy")
    if avg_acc is not None:
        if avg_acc >= 90:
            insights.append(
                f"Prediction accuracy excellent at {avg_acc}%. "
                "Model is well-calibrated."
            )
        elif avg_acc >= 80:
            insights.append(
                f"Prediction accuracy good at {avg_acc}%. "
                "Within target range."
            )
        elif avg_acc >= 75:
            insights.append(
                f"Prediction accuracy at {avg_acc}% - approaching alert threshold. "
                "Review recent data quality and any menu changes."
            )
        else:
            insights.append(
                f"Prediction accuracy LOW at {avg_acc}%. "
                "Investigate: data gaps, menu changes, or unusual events?"
            )

    # Accuracy trend
    trend = accuracy.get("trend")
    if trend == "declining":
        insights.append(
            "Accuracy trending downward. Check for recent changes "
            "(new menu items, staffing patterns, events)."
        )
    elif trend == "improving":
        insights.append("Accuracy trending upward. Model adapting well.")

    # Adoption insights
    adoption_rate = adoption.get("adoption_rate")
    if adoption_rate is not None:
        pct = int(adoption_rate * 100)
        if pct >= 80:
            insights.append(f"Strong adoption at {pct}%. Team engaged.")
        elif pct >= 60:
            insights.append(
                f"Adoption at {pct}% - meeting minimum target. "
                "Look for patterns in skipped recommendations."
            )
        else:
            insights.append(
                f"Adoption at {pct}% - BELOW 60% target. "
                "Check: Are prompts arriving on time? "
                "Are recommendations actionable?"
            )

    # Rating insights
    avg_timing = adoption.get("avg_rush_timing_rating")
    if avg_timing is not None:
        if avg_timing < 3.0:
            insights.append(
                f"Rush timing rated {avg_timing}/5. "
                "Rushes may be arriving earlier/later than predicted."
            )

    avg_helpful = adoption.get("avg_helpfulness_rating")
    if avg_helpful is not None:
        if avg_helpful < 3.0:
            insights.append(
                f"Helpfulness rated {avg_helpful}/5. "
                "Review whether recommendations are adding value."
            )

    # Day-specific patterns
    if daily_details:
        worst_day = min(
            (d for d in daily_details if d["accuracy"] is not None),
            key=lambda d: d["accuracy"],
            default=None,
        )
        if worst_day and worst_day["accuracy"] < 75:
            insights.append(
                f"Worst day: {worst_day['day_name']} ({worst_day['accuracy']}% accuracy). "
                "Check for unusual conditions."
            )

    if not insights:
        insights.append("Insufficient data for insights this week.")

    return insights


# ============================================================
# Report Formatting
# ============================================================


def _format_weekly_report(
    site_name: str,
    week_start: date,
    week_end: date,
    accuracy: dict,
    adoption: dict,
    stats: dict,
    daily_details: list[dict],
    insights: list[str],
) -> str:
    """Format the full weekly report as printable text."""
    sections = []

    # Header
    sections.append(
        f"{'═' * REPORT_WIDTH}\n"
        f"{'CLUBHOUSE AUTOPILOT - WEEKLY OPS REVIEW':^{REPORT_WIDTH}}\n"
        f"{'═' * REPORT_WIDTH}\n"
        f"LOCATION: {site_name}\n"
        f"WEEK: {week_start.strftime('%-d %b')} - {week_end.strftime('%-d %b %Y')}\n"
        f"GENERATED: {date.today().strftime('%A %-d %b %Y')}"
    )

    # Prediction Accuracy
    lines = [
        f"{'─' * REPORT_WIDTH}",
        "PREDICTION ACCURACY",
        f"{'─' * REPORT_WIDTH}",
    ]

    avg_acc = accuracy.get("avg_accuracy")
    days = accuracy.get("days_measured", 0)
    trend = accuracy.get("trend", "unknown")

    if avg_acc is not None:
        lines.append(f"Average: {avg_acc}% ({days} days measured)")
        lines.append(f"Trend: {trend.title()}")
    else:
        lines.append("No accuracy data available this week.")

    # Daily breakdown
    if daily_details:
        lines.append("")
        lines.append(f"{'Day':<12} {'Predicted':>9} {'Rushes':>6} {'Accuracy':>8}")
        lines.append(f"{'─' * 37}")
        for d in daily_details:
            acc_str = f"{d['accuracy']}%" if d['accuracy'] else "—"
            lines.append(
                f"{d['day_name']:<12} {d['predicted_drinks']:>9} {d['rush_count']:>6} {acc_str:>8}"
            )

    sections.append("\n".join(lines))

    # Adoption
    lines = [
        f"{'─' * REPORT_WIDTH}",
        "ADOPTION & FEEDBACK",
        f"{'─' * REPORT_WIDTH}",
    ]

    adoption_rate = adoption.get("adoption_rate")
    if adoption_rate is not None:
        lines.append(
            f"Adoption: {int(adoption_rate * 100)}% "
            f"({adoption['recommendations_adopted']}/{adoption['recommendations_total']})"
        )
    else:
        lines.append("No adoption data this week.")

    avg_timing = adoption.get("avg_rush_timing_rating")
    avg_helpful = adoption.get("avg_helpfulness_rating")
    if avg_timing is not None:
        lines.append(f"Rush Timing Rating: {avg_timing}/5")
    if avg_helpful is not None:
        lines.append(f"Helpfulness Rating: {avg_helpful}/5")

    target_met = adoption.get("meets_target", False)
    lines.append(f"Target (>60%): {'MET' if target_met else 'NOT MET'}")

    sections.append("\n".join(lines))

    # Insights
    lines = [
        f"{'─' * REPORT_WIDTH}",
        "INSIGHTS & ACTIONS",
        f"{'─' * REPORT_WIDTH}",
    ]
    for i, insight in enumerate(insights, 1):
        lines.append(f"{i}. {insight}")

    sections.append("\n".join(lines))

    # Alerts
    if accuracy.get("alert"):
        lines = [
            f"{'─' * REPORT_WIDTH}",
            "⚠ ALERTS",
            f"{'─' * REPORT_WIDTH}",
            accuracy.get("alert_reason", "Review model performance."),
        ]
        sections.append("\n".join(lines))

    # Footer
    sections.append(
        f"{'═' * REPORT_WIDTH}\n"
        f"{'Autopilot v1.2 | ' + site_name:^{REPORT_WIDTH}}\n"
        f"{'═' * REPORT_WIDTH}"
    )

    return "\n".join(sections)


def _format_weekly_sms(
    site_name: str,
    week_start: date,
    week_end: date,
    accuracy: dict,
    adoption: dict,
) -> str:
    """Format a short SMS summary of the weekly review."""
    lines = [
        "Weekly Ops Review",
        f"{week_start.strftime('%-d %b')} - {week_end.strftime('%-d %b')}",
    ]

    avg_acc = accuracy.get("avg_accuracy")
    if avg_acc is not None:
        lines.append(f"Accuracy: {avg_acc}%")

    adoption_rate = adoption.get("adoption_rate")
    if adoption_rate is not None:
        lines.append(f"Adoption: {int(adoption_rate * 100)}%")

    trend = accuracy.get("trend")
    if trend:
        lines.append(f"Trend: {trend}")

    if accuracy.get("alert"):
        lines.append("⚠ Accuracy alert - review needed")

    return "\n".join(lines)


def _format_weekly_roi_report(
    site_name: str,
    week_start: date,
    week_end: date,
    current: dict,
    previous: dict,
    deltas: dict,
    headline: str,
) -> str:
    """Format weekly ROI report as plain text."""
    def _money(cents: Optional[int]) -> str:
        if cents is None:
            return "N/A"
        return f"${cents / 100:,.0f}"

    def _labor_pct(v: Optional[float]) -> str:
        return f"{v:.1f}%" if v is not None else "N/A"

    def _rev_hour(v: Optional[int]) -> str:
        return f"${v / 100:,.0f}" if v is not None else "N/A"

    lines = [
        f"{'═' * REPORT_WIDTH}",
        f"{'CLUBHOUSE AUTOPILOT - WEEKLY ROI':^{REPORT_WIDTH}}",
        f"{'═' * REPORT_WIDTH}",
        f"LOCATION: {site_name}",
        f"WEEK: {week_start.strftime('%-d %b')} - {week_end.strftime('%-d %b %Y')}",
        f"HEADLINE: {headline}",
        "",
        f"{'Current Week':<18} {'Previous Week':<18} {'Delta':<18}",
        f"{'─' * REPORT_WIDTH}",
        (
            f"Revenue {_money(current['total_revenue_cents']):<10} "
            f"{_money(previous['total_revenue_cents']):<15} "
            f"{_money(deltas['revenue_cents_delta']) if deltas['revenue_cents_delta'] is not None else 'N/A'}"
        ),
        (
            f"Net Profit {_money(current['total_net_profit_cents']):<7} "
            f"{_money(previous['total_net_profit_cents']):<15} "
            f"{_money(deltas['net_profit_cents_delta']) if deltas['net_profit_cents_delta'] is not None else 'N/A'}"
        ),
        (
            f"Labor % {_labor_pct(current['avg_labor_pct']):<10} "
            f"{_labor_pct(previous['avg_labor_pct']):<15} "
            f"{str(deltas['labor_pct_delta_pp']) + 'pp' if deltas['labor_pct_delta_pp'] is not None else 'N/A'}"
        ),
        (
            f"Rev/LaborHr {_rev_hour(current['avg_revenue_per_labor_hour_cents']):<6} "
            f"{_rev_hour(previous['avg_revenue_per_labor_hour_cents']):<15} "
            f"{str(deltas['revenue_per_labor_hour_delta_pct']) + '%' if deltas['revenue_per_labor_hour_delta_pct'] is not None else 'N/A'}"
        ),
        "",
        (
            "Data days: "
            f"{current['days']} current, {previous['days']} previous"
        ),
        f"{'═' * REPORT_WIDTH}",
    ]
    return "\n".join(lines)


# ============================================================
# Storage
# ============================================================


def _store_review(
    site_id: str,
    week_start: date,
    week_end: date,
    accuracy: dict,
    adoption: dict,
    insights: list[str],
) -> Optional[str]:
    """Store the weekly review in the database."""
    from sqlalchemy import text

    try:
        with engine.connect() as conn:
            result = conn.execute(
                text(
                    "INSERT INTO weekly_reviews "
                    "(site_id, week_start, week_end, avg_prediction_accuracy, "
                    "avg_adoption_rate, insights, actions_next_week) "
                    "VALUES (:sid, :ws, :we, :acc, :adopt, :insights, :actions) "
                    "ON CONFLICT (site_id, week_start) DO UPDATE SET "
                    "avg_prediction_accuracy = EXCLUDED.avg_prediction_accuracy, "
                    "avg_adoption_rate = EXCLUDED.avg_adoption_rate, "
                    "insights = EXCLUDED.insights, "
                    "actions_next_week = EXCLUDED.actions_next_week, "
                    "generated_at = NOW() "
                    "RETURNING review_id"
                ),
                {
                    "sid": site_id,
                    "ws": week_start,
                    "we": week_end,
                    "acc": accuracy.get("avg_accuracy"),
                    "adopt": adoption.get("adoption_rate"),
                    "insights": json.dumps(insights),
                    "actions": json.dumps([]),
                },
            )
            review_id = str(result.scalar())
            conn.commit()

        logger.info("Stored weekly review %s for %s", review_id, week_start)
        return review_id

    except Exception:
        logger.exception("Failed to store weekly review")
        return None


# ============================================================
# Multi-Site Variance (Section 1.2)
# ============================================================


def generate_multi_site_comparison(
    site_ids: list[str],
    site_names: dict[str, str],
    week_end: date = None,
) -> dict:
    """
    Compare metrics across multiple sites for the same week.

    Spec Section 1.2 output #4: Multi-Site Variance.
    Used when scaling to Location #2+.
    """
    if week_end is None:
        today = date.today()
        days_since_sunday = (today.weekday() + 1) % 7
        week_end = today - timedelta(days=days_since_sunday)

    week_start = week_end - timedelta(days=6)

    sites = []
    for site_id in site_ids:
        accuracy = get_rolling_accuracy(site_id, days_back=7, reference_date=week_end)
        adoption = get_adoption_metrics(site_id, week_start, week_end)

        sites.append({
            "site_id": site_id,
            "site_name": site_names.get(site_id, site_id),
            "avg_accuracy": accuracy.get("avg_accuracy"),
            "adoption_rate": adoption.get("adoption_rate"),
            "avg_timing_rating": adoption.get("avg_rush_timing_rating"),
            "avg_helpful_rating": adoption.get("avg_helpfulness_rating"),
            "alert": accuracy.get("alert", False),
        })

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "sites": sites,
        "site_count": len(sites),
    }
