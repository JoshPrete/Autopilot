"""
Clubhouse Autopilot v1.2 - SMS Prompt Library
Complete prompt templates from Spec Section 9.1

All SMS messages sent by Autopilot are assembled here.
Templates use Clubhouse language and are kept short for SMS
readability. Each function returns the message body string.
"""

# ============================================================
# Tomorrow Plan Summary (6:00pm)
# ============================================================


def tomorrow_plan_summary(
    day_name: str,
    date_str: str,
    rush_windows: list[dict],
    weather: dict = None,
    total_drinks: int = 0,
    staffing_mode: str = None,
) -> str:
    """
    SMS sent at 6pm with tomorrow's headline forecast.

    Spec Section 9.1 - Tomorrow Plan Summary:
        Tomorrow Plan Ready
        FRI 7 FEB
        Rush: 8:47-9:32am (52 drinks)
        Weather: 28°C sunny
        3P workflow needed
        Print on bench
        Reply HELP for questions
    """
    lines = [
        "Tomorrow Plan Ready",
        f"{day_name[:3].upper()} {date_str}",
    ]

    if rush_windows:
        for i, rush in enumerate(rush_windows):
            start = _format_time(rush["start"])
            end = _format_time(rush["end"])
            drinks = rush.get("predicted_drinks", "?")
            lines.append(f"Rush: {start}-{end} ({drinks} drinks)")
    else:
        lines.append(f"No rush windows detected")

    if weather:
        temp = weather.get("temp_c", "?")
        desc = weather.get("description", "")
        lines.append(f"Weather: {temp}°C {desc}")

    if staffing_mode and staffing_mode in ("3P", "4P"):
        lines.append(f"{staffing_mode} workflow needed")

    lines.append("Print on bench")
    lines.append("Reply HELP for questions")

    return "\n".join(lines)


# ============================================================
# Pre-Rush Reminder (T-15 min)
# ============================================================


def pre_rush_reminder(
    rush_start: str,
    rush_end: str,
    predicted_drinks: int,
    confidence_label: str,
    switch_3p_time: str = None,
) -> str:
    """
    SMS sent 15 minutes before rush start.

    Spec Section 9.1 - Pre-Rush Reminder:
        Rush in 15 mins
        Actions:
        Wally start now
        Switch 3P at 8:40
        Pre-stage cups
        See printed plan
    """
    start_fmt = _format_time(rush_start)

    lines = [
        f"Rush in 15 mins ({predicted_drinks} drinks, {confidence_label} confidence)",
        "Actions:",
        "Wally start now",
    ]

    if switch_3p_time:
        lines.append(f"Switch 3P at {_format_time(switch_3p_time)}")

    lines.extend(
        [
            "Pre-stage cups",
            "See printed plan",
        ]
    )

    return "\n".join(lines)


# ============================================================
# Switch to 3P (T-7 min)
# ============================================================


def switch_to_3p(
    staff_names: dict,
    rush_start: str,
) -> str:
    """
    SMS sent 7 minutes before rush start.

    Spec Section 9.1 - Switch to 3P:
        Switch to 3P workflow NOW
        • Sarah (P1): Front
        • Tom (P2): Shots+Milk
        • Jessica (P3): Delivery
        Rush starts 8:47am
    """
    p1 = staff_names.get("P1", "P1")
    p2 = staff_names.get("P2", "P2")
    p3 = staff_names.get("P3", "P3")

    lines = [
        "Switch to 3P workflow NOW",
        f"• {p1} (P1): Front",
        f"• {p2} (P2): Shots+Milk",
        f"• {p3} (P3): Delivery",
        f"Rush starts {_format_time(rush_start)}",
    ]

    return "\n".join(lines)


# ============================================================
# Wally Start Cycling
# ============================================================


def wally_start(
    volume_litres: int,
    split: dict,
    staff_name: str = "P2",
) -> str:
    """
    SMS sent to P2 (milk operator) to start Wally cycling.

    Spec Section 9.1 - Wally Start Cycling:
        P2: Start Wally cycling
        8L milk now:
        • 5L full cream
        • 2L oat
        • 1L soy
        Keep running until rush ends
    """
    lines = [
        f"{staff_name}: Start Wally cycling",
        f"{volume_litres}L milk now:",
    ]

    for milk_type, litres in split.items():
        label = milk_type.replace("_", " ").title()
        lines.append(f"• {litres}L {label}")

    lines.append("Keep running until rush ends")

    return "\n".join(lines)


# ============================================================
# P1 to Shots
# ============================================================


def p1_to_shots() -> str:
    """
    SMS sent when counter is clear and P1 should move to shots.

    Spec Section 9.1 - P1 to Shots:
        Counter clear
        P1 move to shots now
    """
    return "Counter clear\nP1 move to shots now"


# ============================================================
# Delivery Override
# ============================================================


def delivery_override(staff_name: str = "P3") -> str:
    """
    SMS sent to P3 when drinks are stacking up.

    Spec Section 9.1 - Delivery Override:
        Drinks stacking
        P3: Delivery priority NOW
        P1 stay on shots
    """
    return f"Drinks stacking\n{staff_name}: Delivery priority NOW\nP1 stay on shots"


# ============================================================
# Rush Easing / End
# ============================================================


def rush_easing() -> str:
    """
    SMS sent to manager when rush is ending.

    Spec Section 9.1 - Rush Easing:
        Rush easing
        Return to 2P baseline
        P3 can transition out
        Good work!
    """
    return "Rush easing\nReturn to 2P baseline\nP3 can transition out\nGood work!"


# ============================================================
# End of Day Feedback Request (3:00pm)
# ============================================================


def end_of_day_feedback() -> str:
    """
    SMS sent at 3pm requesting daily ratings.

    Spec Section 9.1 - End of Day Feedback:
        How was today's plan?
        Reply 2 numbers:
        1. Rush timing (1-5)
        2. Helpfulness (1-5)
        Example: "5 4"
    """
    return (
        "How was today's plan?\n"
        "Reply 2 numbers:\n"
        "1. Rush timing (1-5)\n"
        "2. Helpfulness (1-5)\n"
        'Example: "5 4"'
    )


# ============================================================
# System Alerts (Fail Quiet - Spec Section 1.3)
# ============================================================


def system_alert_ingestion_failed(site_name: str, error: str = "") -> str:
    """Alert when data ingestion fails."""
    msg = f"Autopilot Alert: Data ingestion failed for {site_name}."
    if error:
        msg += f"\n{error[:100]}"
    msg += "\nFalling back to printed SOP."
    return msg


def system_alert_sms_failed(site_name: str) -> str:
    """Alert when SMS delivery fails."""
    return (
        f"Autopilot Alert: SMS delivery failing for {site_name}.\n"
        "Plan generated but nudges won't arrive.\n"
        "Use printed plan + verbal comms."
    )


def system_alert_prediction_blocked(
    site_name: str,
    run_date: str,
    reasons: list[str],
) -> str:
    """Alert when prediction/plan is blocked by partial ingest safeguards."""
    reason_line = "; ".join([r for r in reasons if r])[:120] if reasons else "partial_ingest"
    return (
        f"Autopilot BLOCKED: {site_name} {run_date}\n"
        f"Reason: {reason_line}\n"
        "No tomorrow plan sent. Re-run ingest, then predict."
    )


# ============================================================
# Helpers
# ============================================================


def _format_time(iso_str: str) -> str:
    """
    Format an ISO datetime string to a short time like '8:47am'.

    Handles both full ISO format and time-only strings.
    """
    from datetime import datetime

    try:
        if "T" in iso_str:
            dt = datetime.fromisoformat(iso_str)
        else:
            dt = datetime.fromisoformat(f"2000-01-01T{iso_str}")

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
        return iso_str
