"""
Deterministic builder/renderer for the daily Tomorrow Plan report.

This module intentionally avoids model-side changes and keeps logic explicit:
- forecast revenue comes from predicted drinks x trailing revenue/drink baseline
- wage risk is threshold-based (green/amber/red)
- one recommendation is selected with simple rule logic
"""

from __future__ import annotations

from datetime import datetime

WAGE_RISK_GREEN_MAX = 30.0
WAGE_RISK_AMBER_MAX = 35.0


def normalize_confidence_label(score: float | None) -> str:
    if score is None:
        return "unknown"
    if score >= 0.8:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def classify_wage_risk(wage_pct: float | None) -> str:
    if wage_pct is None:
        return "amber"
    if wage_pct <= WAGE_RISK_GREEN_MAX:
        return "green"
    if wage_pct <= WAGE_RISK_AMBER_MAX:
        return "amber"
    return "red"


def classify_workload_band(predicted_workload: float | int | None, duration_minutes: int | None) -> str:
    workload = float(predicted_workload or 0.0)
    duration = max(15, int(duration_minutes or 15))
    intervals = max(1, round(duration / 15))
    per_interval = workload / intervals

    if per_interval < 14:
        return "steady"
    if per_interval < 22:
        return "busy"
    if per_interval < 30:
        return "heavy"
    return "peak"


def choose_one_action(wage_risk: str, rush_windows: list[dict]) -> str:
    sorted_rushes = sorted(rush_windows, key=lambda r: str(r.get("start", "")))
    first_start = sorted_rushes[0].get("start") if sorted_rushes else None
    first_time = _short_time(first_start) if first_start else None

    if wage_risk == "red":
        if first_time:
            return (
                f"Trim 1 labor-hour from the lowest-load block after {first_time}, "
                "but protect pre-rush setup coverage."
            )
        return "Trim 1 labor-hour from the lowest-load block while preserving service flow."

    if wage_risk == "amber":
        if first_time:
            return (
                f"Move one team member to start 30 minutes before {first_time} "
                "instead of adding hours."
            )
        return "Re-time one start to the busiest expected period before adding hours."

    if first_time:
        return (
            f"Keep labor flat and run a pre-rush prep reset 15 minutes before {first_time} "
            "(cups, milk, shots ready)."
        )
    return "Keep labor flat and focus on fast handoff execution at peak demand windows."


def build_tomorrow_report_payload(
    *,
    site_name: str,
    site_id: str,
    forecast_date: str,
    prediction_id: str,
    predicted_drinks: int,
    forecast_revenue_cents: int,
    confidence_score: float | None,
    confidence_label: str | None,
    rush_windows: list[dict],
    scheduled_labor_cents: int,
    wage_pct: float | None,
    baseline_days: int,
    baseline_revenue_per_drink_cents: float,
    generated_at: str | None = None,
) -> dict:
    normalized_label = confidence_label or normalize_confidence_label(confidence_score)
    enriched_rush = []
    for window in rush_windows:
        enriched_rush.append(
            {
                "start": window.get("start"),
                "end": window.get("end"),
                "predicted_drinks": int(window.get("predicted_drinks") or 0),
                "predicted_workload": float(window.get("predicted_workload") or 0.0),
                "duration_minutes": int(window.get("duration_minutes") or 0),
                "band": classify_workload_band(
                    window.get("predicted_workload"),
                    window.get("duration_minutes"),
                ),
            }
        )

    wage_risk = classify_wage_risk(wage_pct)
    return {
        "site_name": site_name,
        "site_id": site_id,
        "forecast_date": forecast_date,
        "prediction_id": prediction_id,
        "generated_at": generated_at or datetime.now().isoformat(timespec="seconds"),
        "predicted_drinks": int(predicted_drinks),
        "forecast_revenue_cents": int(forecast_revenue_cents),
        "confidence_score": float(confidence_score) if confidence_score is not None else None,
        "confidence_label": normalized_label,
        "rush_windows": enriched_rush,
        "scheduled_labor_cents": int(scheduled_labor_cents),
        "wage_pct": float(wage_pct) if wage_pct is not None else None,
        "wage_risk": wage_risk,
        "recommended_action": choose_one_action(wage_risk, enriched_rush),
        "baseline_days": int(baseline_days),
        "baseline_revenue_per_drink_cents": float(baseline_revenue_per_drink_cents),
    }


def render_tomorrow_report_markdown(payload: dict) -> str:
    revenue_dollars = payload["forecast_revenue_cents"] / 100.0
    labor_dollars = payload["scheduled_labor_cents"] / 100.0
    conf_score = payload.get("confidence_score")
    conf_pct = f"{round(conf_score * 100)}%" if conf_score is not None else "unknown"
    wage_pct = payload.get("wage_pct")
    wage_pct_text = f"{wage_pct:.1f}%" if wage_pct is not None else "unknown"
    risk_text = str(payload.get("wage_risk", "amber")).upper()
    baseline_per_drink = payload["baseline_revenue_per_drink_cents"] / 100.0

    lines = [
        f"# Tomorrow Plan - {payload['forecast_date']}",
        "",
        f"- Site: {payload['site_name']} ({payload['site_id']})",
        f"- Prediction ID: {payload['prediction_id']}",
        f"- Generated: {payload['generated_at']}",
        "",
        "## 1) Forecast Revenue + Confidence",
        f"- Forecast revenue: ${revenue_dollars:,.2f}",
        f"- Confidence: {payload['confidence_label'].title()} ({conf_pct})",
        f"- Predicted drinks: {payload['predicted_drinks']}",
        (
            "- Revenue-per-drink baseline: "
            f"${baseline_per_drink:.2f} from {payload['baseline_days']} recent days"
        ),
        "",
        "## 2) Predicted Rush Windows / Workload Bands",
    ]

    rush_windows = payload.get("rush_windows", [])
    if rush_windows:
        lines.extend(
            [
                "| Window | Predicted drinks | Predicted workload | Workload band |",
                "|---|---:|---:|---|",
            ]
        )
        for rush in rush_windows:
            start = _short_time(rush.get("start"))
            end = _short_time(rush.get("end"))
            lines.append(
                f"| {start}-{end} | {rush['predicted_drinks']} | "
                f"{rush['predicted_workload']:.1f} | {rush['band']} |"
            )
    else:
        lines.append("- No rush windows detected.")

    lines.extend(
        [
            "",
            "## 3) Wage% Risk Flag",
            f"- Scheduled labor cost: ${labor_dollars:,.2f}",
            f"- Forecast wage %: {wage_pct_text}",
            f"- Risk flag: **{risk_text}**",
            "",
            "## 4) One Recommended Action",
            f"- {payload['recommended_action']}",
            "",
        ]
    )

    return "\n".join(lines)


def _short_time(value: str | None) -> str:
    if not value:
        return "unknown"

    cleaned = str(value).replace("Z", "+00:00")
    for parser in (datetime.fromisoformat,):
        try:
            dt = parser(cleaned)
            return dt.strftime("%H:%M")
        except ValueError:
            continue
    return str(value)
