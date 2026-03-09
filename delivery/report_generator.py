"""
Report generator — Delivery layer.

Renders the Tomorrow Plan markdown file from intelligence signals
and decision layer actions.

Pure formatting — no DB queries, no API calls.
"""

from __future__ import annotations

from datetime import date, datetime
from pathlib import Path


def generate_report(
    signals: dict,
    actions: list[str],
    forecast_date: date,
    reports_dir: Path,
    site_name: str = "Clubhouse",
) -> Path:
    """
    Write reports/tomorrow_YYYY-MM-DD.md and return the path.

    Args:
        signals: output of the intelligence layer
        actions: output of the decision layer
        forecast_date: the date being planned for
        reports_dir: directory to write into
        site_name: display name for the site
    """
    content = _render(signals, actions, forecast_date, site_name)
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"tomorrow_{forecast_date.isoformat()}.md"
    path.write_text(content, encoding="utf-8")
    return path


def _render(
    signals: dict,
    actions: list[str],
    forecast_date: date,
    site_name: str,
) -> str:
    revenue = int(signals.get("predicted_cents") or 0) / 100
    labor = int(signals.get("scheduled_labor_cents") or 0) / 100
    wage_pct = float(signals.get("wage_pct") or 0)
    risk = signals.get("labor_risk", "green").upper()
    drinks = int(signals.get("predicted_drinks") or 0)
    conf_label = signals.get("label", "medium").title()
    conf_pct = round(float(signals.get("confidence") or 0.5) * 100)
    based_on = signals.get("based_on_days", 0)
    staff = int(signals.get("staff_count") or 0)
    hours = float(signals.get("total_hours") or 0)

    day_str = forecast_date.strftime("%A, %d %B %Y")
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        f"# TOMORROW PLAN — {site_name}",
        f"### {day_str}",
        f"*Generated {generated}*",
        "",
        "---",
        "",
        "## Forecast Revenue",
        f"**${revenue:,.0f}** forecast revenue",
        f"- Predicted drinks: {drinks}",
        f"- Confidence: {conf_label} ({conf_pct}%)"
        + (f" — based on {based_on} days of history" if based_on else ""),
        "",
        "---",
        "",
        "## Rush Windows",
    ]

    rush_windows = signals.get("rush_windows", [])
    if rush_windows:
        for w in rush_windows:
            start = _fmt_time(w.get("start"))
            end = _fmt_time(w.get("end"))
            band = w.get("band", "").title()
            w_drinks = int(w.get("predicted_drinks") or 0)
            lines.append(f"**{start}–{end}** &nbsp; {band} — {w_drinks} drinks")
    else:
        lines.append("No significant rush windows detected.")

    lines += [
        "",
        "---",
        "",
        "## Labour Pressure",
        f"- Scheduled labor: **${labor:,.0f}**  ({wage_pct:.1f}% of forecast revenue)",
        f"- Staff rostered: {staff} ({hours:.0f}h total)",
        f"- Risk flag: **{risk}**",
        "",
        "---",
        "",
        "## Actions for Tomorrow",
    ]

    for i, action in enumerate(actions, 1):
        lines.append(f"**{i}.** {action}")

    lines += [
        "",
        "---",
        f"*Clubhouse Autopilot · {generated}*",
    ]

    return "\n".join(lines)


def _fmt_time(iso_value: str | None) -> str:
    if not iso_value:
        return "unknown"
    try:
        cleaned = str(iso_value).replace("Z", "+00:00")
        return datetime.fromisoformat(cleaned).strftime("%H:%M")
    except ValueError:
        return str(iso_value)
