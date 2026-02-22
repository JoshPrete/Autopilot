"""
Clubhouse Autopilot v1.2 - Tomorrow Plan Generator
Generates the printable daily plan (Spec Section 8.1)

The Tomorrow Plan is the primary output of Autopilot - a printed
sheet placed on the bench each morning. This module generates it
as plain text (for terminal/thermal printers) and optionally as
HTML for PDF conversion.
"""

import logging
from datetime import date, datetime
from typing import Optional

from models.prediction import get_yesterday_recap
from models.recommendations import (
    build_wally_plan,
    generate_pre_rush_checklist,
    get_role_assignments,
)

logger = logging.getLogger("autopilot.tomorrow_plan")

# Plan width for text formatting
PLAN_WIDTH = 59


# ============================================================
# Main Plan Generator
# ============================================================


def generate_tomorrow_plan(
    site_name: str,
    site_id: str,
    prediction: dict,
    staff_names: dict = None,
    generated_at: datetime = None,
) -> str:
    """
    Generate the complete Tomorrow Plan as formatted text.

    Matches the exact template from Spec Section 8.1:
        ═══════════════════════════════════════════════════════════
        CLUBHOUSE AUTOPILOT - TOMORROW PLAN
        ═══════════════════════════════════════════════════════════
        LOCATION: Clubhouse Nundah
        DATE: Friday, 7 February 2026
        GENERATED: Thursday 6 Feb, 6:00pm
        ...

    Args:
        site_name: Display name (e.g. "Clubhouse Nundah")
        site_id: Site UUID (for DB lookups)
        prediction: Full prediction dict from generate_prediction()
        staff_names: Optional role -> name mapping
        generated_at: Override generation timestamp (default: now)

    Returns:
        Complete formatted plan as string
    """
    if staff_names is None:
        staff_names = {}
    if generated_at is None:
        generated_at = datetime.now()

    forecast = prediction.get("forecast", {})
    weather = prediction.get("weather")
    rush_windows = prediction.get("rush_windows", [])
    total_drinks = prediction.get("total_predicted_drinks", 0)
    staffing_mode = prediction.get("staffing_mode")
    staff_scheduled = prediction.get("staff_scheduled")

    forecast_date_str = forecast.get("forecast_date", "")
    day_name = forecast.get("day_name", "")
    forecast_date = None

    # Format the forecast date for display
    try:
        forecast_date = date.fromisoformat(forecast_date_str)
        date_display = forecast_date.strftime("%A, %-d %B %Y")
    except (ValueError, TypeError):
        date_display = forecast_date_str

    # Format generation time
    gen_display = generated_at.strftime("%A %-d %b, %-I:%M%p").lower()
    # Capitalize first letter
    gen_display = gen_display[0].upper() + gen_display[1:]

    sections = []

    # ── Header ──
    sections.append(_header(site_name, date_display, gen_display))

    # ── Forecast Conditions ──
    sections.append(
        _forecast_conditions(
            weather,
            day_name,
            forecast,
            staff_scheduled,
            staff_names,
        )
    )

    # ── Yesterday Recap ──
    recap = get_yesterday_recap(site_id, today=forecast_date or date.today())
    if recap:
        sections.append(_yesterday_recap(recap))

    # ── Rush Windows ──
    for i, rush in enumerate(rush_windows):
        wally_plan = build_wally_plan([rush])
        checklist = generate_pre_rush_checklist(rush)
        state = "S3P_RUSH" if staffing_mode in ("3P", "4P") else "S2P_STANDARD"
        assignments = get_role_assignments(staff_names, state)

        sections.append(
            _rush_window_section(
                window_num=i + 1,
                rush=rush,
                wally_plan=wally_plan,
                assignments=assignments,
                checklist=checklist,
                staff_names=staff_names,
            )
        )

    # ── Targets ──
    sections.append(_targets_section(total_drinks))

    # ── Feedback ──
    sections.append(_feedback_section())

    # ── Footer ──
    sections.append(_footer(site_name))

    return "\n".join(sections)


# ============================================================
# Section Builders
# ============================================================


def _header(site_name: str, date_display: str, gen_display: str) -> str:
    return (
        f"{'═' * PLAN_WIDTH}\n"
        f"{'CLUBHOUSE AUTOPILOT - TOMORROW PLAN':^{PLAN_WIDTH}}\n"
        f"{'═' * PLAN_WIDTH}\n"
        f"LOCATION: {site_name}\n"
        f"DATE: {date_display}\n"
        f"GENERATED: {gen_display}"
    )


def _forecast_conditions(
    weather: Optional[dict],
    day_name: str,
    forecast: dict,
    staff_scheduled: Optional[int],
    staff_names: dict,
) -> str:
    lines = [
        f"{'─' * PLAN_WIDTH}",
        "FORECAST CONDITIONS",
        f"{'─' * PLAN_WIDTH}",
    ]

    # Weather
    if weather:
        temp = weather.get("temp_c", "?")
        desc = weather.get("description", "unknown").title()
        rain_prob = weather.get("rain_probability", 0)
        rain_pct = int(rain_prob * 100) if rain_prob <= 1 else int(rain_prob)
        lines.append(f"Weather: {temp}°C, {desc}, {rain_pct}% rain")
    else:
        lines.append("Weather: Unavailable")

    # Day comparison
    event_mult = forecast.get("event_multiplier", 1.0)
    if event_mult != 1.0:
        pct = int((event_mult - 1.0) * 100)
        sign = "+" if pct > 0 else ""
        lines.append(f"Day: {day_name} (event impact: {sign}{pct}%)")
    else:
        lines.append(f"Day: {day_name}")

    # Staff
    if staff_scheduled:
        names_list = [
            staff_names.get(role, "")
            for role in ["P1", "P2", "P3", "MANAGER"]
            if staff_names.get(role)
        ]
        staff_str = f"{staff_scheduled} FTE"
        if names_list:
            staff_str += f" ({', '.join(names_list)})"
        lines.append(f"Staff Scheduled: {staff_str}")

    return "\n".join(lines)


def _yesterday_recap(recap: dict) -> str:
    lines = [
        f"{'─' * PLAN_WIDTH}",
        "YESTERDAY RECAP",
        f"{'─' * PLAN_WIDTH}",
    ]

    predicted = recap.get("predicted_drinks", 0)
    accuracy = recap.get("actual_accuracy")

    if accuracy is not None:
        lines.append(f"Predicted: {predicted} drinks | Accuracy: {accuracy}%")
    else:
        lines.append(f"Predicted: {predicted} drinks | Actual: pending")

    total_recs = recap.get("recommendations_total", 0)
    adopted = recap.get("recommendations_adopted", 0)
    if total_recs > 0:
        lines.append(f"Followed: {adopted}/{total_recs} recommendations")

    adoption_rate = recap.get("adoption_rate")
    if adoption_rate is not None:
        lines.append(f"Adoption: {int(adoption_rate * 100)}%")

    return "\n".join(lines)


def _rush_window_section(
    window_num: int,
    rush: dict,
    wally_plan: dict,
    assignments: list[dict],
    checklist: list[str],
    staff_names: dict,
) -> str:
    start = _format_time(rush["start"])
    end = _format_time(rush["end"])
    duration = rush.get("duration_minutes", "?")
    predicted_drinks = rush.get("predicted_drinks", "?")
    predicted_workload = rush.get("predicted_workload", "?")
    confidence = rush.get("confidence", 0)
    confidence_pct = int(confidence * 100) if confidence <= 1 else int(confidence)

    lines = [
        f"{'─' * PLAN_WIDTH}",
        f"RUSH WINDOW #{window_num}: {start} - {end} ({duration} min)",
        f"{'─' * PLAN_WIDTH}",
        f"EXPECTED: {predicted_drinks} drinks | {predicted_workload} workload units",
        f"CONFIDENCE: {confidence_pct}%",
    ]

    # Actions
    wally_start = _format_time(rush.get("wally_start_time", rush["start"]))
    switch_time = _format_time(rush.get("switch_3p_time", rush["start"]))

    lines.append(f"ACTIONS (execute before {start}):")

    # 1. Wally
    action_num = 1
    if wally_plan.get("batches"):
        batch = wally_plan["batches"][0]
        vol = batch.get("volume_litres", 0)
        split = batch.get("split", {})
        p2_name = staff_names.get("P2", "P2")

        lines.append(f"{action_num}. WALLY START ({wally_start})")
        lines.append(f"   • {p2_name}: Start Wally cycling")
        split_parts = []
        for milk_type, litres in split.items():
            label = milk_type.replace("_", " ").title()
            split_parts.append(f"{litres}L {label.lower()}")
        lines.append(f"   • Volume: {vol}L ({', '.join(split_parts)})")
        action_num += 1

    # 2. Switch to 3P
    lines.append(f"{action_num}. SWITCH TO 3P ({switch_time})")
    for assignment in assignments:
        role = assignment["role"]
        name = assignment["name"]
        primary = assignment["primary"]
        lines.append(f"   • {name} ({role}): {primary}")
    action_num += 1

    # 3. P3 Prep
    p3_name = staff_names.get("P3", "P3")
    lines.append(f"{action_num}. P3 PREP ({switch_time})")
    lines.append(f"   • Pre-stage 30 cups at bar")
    action_num += 1

    # 4. Delivery Priority
    lines.append(f"{action_num}. DELIVERY PRIORITY ({start} onwards)")
    lines.append(f"   • {p3_name}: Deliver as soon as ready")
    lines.append(f"   • Priority: Delivery > Prep")

    # Pre-Rush Checklist
    checklist_time = _format_time(rush.get("alert_time", rush["start"]))
    lines.append(f"PRE-RUSH CHECKLIST (by {checklist_time}):")
    for item in checklist:
        lines.append(f"□ {item}")

    return "\n".join(lines)


def _targets_section(total_drinks: int) -> str:
    # Rough revenue estimate (~$8.15 avg per drink for specialty coffee)
    est_revenue = int(total_drinks * 8.15)
    # Target drinks per hour (11 operating hours, 5am-4pm)
    dph = round(total_drinks / 11, 0) if total_drinks else 0

    lines = [
        f"{'─' * PLAN_WIDTH}",
        "TARGETS",
        f"{'─' * PLAN_WIDTH}",
        f"Total drinks: {total_drinks}",
        f"Revenue: ~${est_revenue:,}",
        f"Labour: ≤33%",
        f"Drinks/hour: >{int(dph)}",
    ]
    return "\n".join(lines)


def _feedback_section() -> str:
    lines = [
        f"{'─' * PLAN_WIDTH}",
        "FEEDBACK",
        f"{'─' * PLAN_WIDTH}",
        "End of day: Text ratings 1-5",
        "Rush timing: __",
        "Helpfulness: __",
    ]
    return "\n".join(lines)


def _footer(site_name: str) -> str:
    return (
        f"{'═' * PLAN_WIDTH}\n"
        f"{'Autopilot v1.2 | ' + site_name:^{PLAN_WIDTH}}\n"
        f"{'═' * PLAN_WIDTH}"
    )


# ============================================================
# HTML Version (for PDF generation)
# ============================================================


def generate_tomorrow_plan_html(
    site_name: str,
    site_id: str,
    prediction: dict,
    staff_names: dict = None,
    generated_at: datetime = None,
) -> str:
    """
    Generate the Tomorrow Plan as HTML for PDF printing.

    Wraps the text plan in a simple HTML template styled for
    A4 printing. Uses monospace font to preserve formatting.
    """
    text_plan = generate_tomorrow_plan(
        site_name=site_name,
        site_id=site_id,
        prediction=prediction,
        staff_names=staff_names,
        generated_at=generated_at,
    )

    # Escape HTML entities
    html_content = (
        text_plan.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("═", "=")
        .replace("─", "-")
        .replace("□", "[ ]")
    )

    html = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Tomorrow Plan - {site_name}</title>
<style>
    @page {{
        size: A4;
        margin: 15mm;
    }}
    body {{
        font-family: 'Courier New', Courier, monospace;
        font-size: 11pt;
        line-height: 1.4;
        color: #000;
        white-space: pre;
        max-width: 100%;
    }}
    @media print {{
        body {{
            font-size: 10pt;
        }}
    }}
</style>
</head>
<body>{html_content}</body>
</html>"""

    return html


# ============================================================
# Helpers
# ============================================================


def _format_time(iso_str: str) -> str:
    """Format ISO datetime to short time like '8:47am'."""
    try:
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str)
        else:
            return iso_str

        hour = dt.hour
        minute = dt.minute
        period = "am" if hour < 12 else "pm"
        display_hour = hour % 12
        if display_hour == 0:
            display_hour = 12

        if minute == 0:
            return f"{display_hour}{period}"
        return f"{display_hour}:{minute:02d}{period}"
    except (ValueError, TypeError):
        return str(iso_str)
