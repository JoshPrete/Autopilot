"""
Clubhouse Autopilot v1.2 - SMS Sender
Twilio SMS dispatch with delivery tracking (Spec Section 7.4)

Handles all outbound SMS delivery including:
- Individual message sending via Twilio
- Role-based routing (look up phone by P1/P2/P3/MANAGER)
- Batch dispatch for recommendation lists
- Delivery status tracking
- Fail Quiet behavior (Section 1.3)
"""

import logging
from datetime import datetime
from typing import Optional

from config.settings import settings
from data.storage import get_contacts_by_role
from delivery import sms_prompts

logger = logging.getLogger("autopilot.sender")


# ============================================================
# Core SMS Sending
# ============================================================


def send_sms(to_number: str, message_body: str) -> Optional[str]:
    """
    Send a single SMS via Twilio.

    Per Spec Section 7.4:
        client.messages.create(
            body=message_body,
            from_=TWILIO_PHONE_NUMBER,
            to=to_number
        )

    Returns Twilio message SID on success, None on failure.
    Follows Fail Quiet principle: logs error but doesn't raise.
    """
    if not settings.TWILIO_ACCOUNT_SID or not settings.TWILIO_AUTH_TOKEN:
        logger.warning("Twilio not configured, skipping SMS to %s", to_number)
        return None

    if not settings.TWILIO_PHONE_NUMBER:
        logger.warning("No TWILIO_PHONE_NUMBER configured")
        return None

    try:
        from twilio.rest import Client

        client = Client(
            settings.TWILIO_ACCOUNT_SID,
            settings.TWILIO_AUTH_TOKEN,
        )

        message = client.messages.create(
            body=message_body,
            from_=settings.TWILIO_PHONE_NUMBER,
            to=to_number,
        )

        logger.info(
            "SMS sent to %s (SID: %s, %d chars)",
            to_number,
            message.sid,
            len(message_body),
        )
        return message.sid

    except Exception:
        logger.exception("Failed to send SMS to %s", to_number)
        return None


# ============================================================
# Role-Based Routing
# ============================================================


def send_to_role(
    site_id: str,
    role: str,
    message_body: str,
) -> list[dict]:
    """
    Send SMS to all active contacts with a given role at a site.

    Looks up contacts from the contacts table by role_label
    (P1, P2, P3, MANAGER) and sends to each.

    Returns list of delivery results.
    """
    contacts = get_contacts_by_role(site_id, role)

    if not contacts:
        logger.warning("No active contacts for role %s at site %s", role, site_id)
        return []

    results = []
    for contact in contacts:
        phone = contact["phone_e164"]
        name = contact["full_name"]
        sid = send_sms(phone, message_body)
        results.append(
            {
                "contact_id": contact["contact_id"],
                "name": name,
                "phone": phone,
                "role": role,
                "message_sid": sid,
                "delivered": sid is not None,
                "sent_at": datetime.utcnow().isoformat(),
            }
        )

    delivered = sum(1 for r in results if r["delivered"])
    logger.info(
        "Sent to role %s: %d/%d delivered",
        role,
        delivered,
        len(results),
    )
    return results


def send_to_manager(site_id: str, message_body: str) -> list[dict]:
    """Send SMS to manager(s) at a site."""
    # Try contacts table first
    results = send_to_role(site_id, "MANAGER", message_body)

    # Fallback to settings.MANAGER_PHONE if no contacts found
    if not results and settings.MANAGER_PHONE:
        sid = send_sms(settings.MANAGER_PHONE, message_body)
        results = [
            {
                "contact_id": None,
                "name": "Manager (fallback)",
                "phone": settings.MANAGER_PHONE,
                "role": "MANAGER",
                "message_sid": sid,
                "delivered": sid is not None,
                "sent_at": datetime.utcnow().isoformat(),
            }
        ]

    return results


# ============================================================
# Recommendation Dispatch
# ============================================================


def dispatch_recommendation(
    site_id: str,
    recommendation: dict,
    staff_names: dict = None,
) -> list[dict]:
    """
    Dispatch a single recommendation as SMS to the appropriate role.

    Maps action_type to the correct SMS prompt template and sends
    to the owner_role's contacts.

    Args:
        site_id: Site UUID
        recommendation: Recommendation dict from generate_*_recommendations()
        staff_names: Optional role -> name mapping for personalization

    Returns:
        List of delivery results
    """
    if staff_names is None:
        staff_names = {}

    action_type = recommendation["action_type"]
    owner_role = recommendation["owner_role"]
    details = recommendation.get("action_details", {})

    # Build the SMS body from the prompt template
    message = _build_message(action_type, details, staff_names)

    if not message:
        logger.warning("No SMS template for action_type: %s", action_type)
        return []

    return send_to_role(site_id, owner_role, message)


def dispatch_recommendations_batch(
    site_id: str,
    recommendations: list[dict],
    staff_names: dict = None,
) -> dict:
    """
    Dispatch a batch of recommendations, each to its owner role.

    Filters to only recommendations that are due (action_timing <= now).

    Returns summary of all delivery attempts.
    """
    now = datetime.utcnow()
    all_results = []
    sent_count = 0
    skipped_count = 0

    for rec in recommendations:
        # Check if this recommendation is due
        timing_str = rec.get("action_timing", "")
        if timing_str:
            try:
                action_time = datetime.fromisoformat(timing_str)
                if action_time > now:
                    skipped_count += 1
                    continue
            except (ValueError, TypeError):
                pass

        results = dispatch_recommendation(site_id, rec, staff_names)
        all_results.extend(results)
        if results:
            sent_count += 1

    delivered = sum(1 for r in all_results if r["delivered"])

    logger.info(
        "Batch dispatch: %d sent, %d skipped (not due), %d/%d delivered",
        sent_count,
        skipped_count,
        delivered,
        len(all_results),
    )

    return {
        "recommendations_sent": sent_count,
        "recommendations_skipped": skipped_count,
        "messages_total": len(all_results),
        "messages_delivered": delivered,
        "results": all_results,
    }


# ============================================================
# High-Level Dispatch Functions
# ============================================================


def send_tomorrow_plan_sms(
    site_id: str,
    prediction: dict,
    staff_names: dict = None,
) -> list[dict]:
    """
    Send the 6pm Tomorrow Plan summary SMS to manager.

    This is the primary daily SMS that tells the manager
    tomorrow's forecast is ready and should be printed.
    """
    forecast = prediction.get("forecast", {})
    day_name = forecast.get("day_name", "")
    date_str = forecast.get("forecast_date", "")
    rush_windows = prediction.get("rush_windows", [])
    weather = prediction.get("weather")
    total_drinks = prediction.get("total_predicted_drinks", 0)
    staffing_mode = prediction.get("staffing_mode")

    message = sms_prompts.tomorrow_plan_summary(
        day_name=day_name,
        date_str=date_str,
        rush_windows=rush_windows,
        weather=weather,
        total_drinks=total_drinks,
        staffing_mode=staffing_mode,
    )

    return send_to_manager(site_id, message)


def send_feedback_request(site_id: str) -> list[dict]:
    """Send the 3pm end-of-day feedback request to manager."""
    message = sms_prompts.end_of_day_feedback()
    return send_to_manager(site_id, message)


def send_system_alert(site_id: str, alert_type: str, **kwargs) -> list[dict]:
    """
    Send a system alert SMS to manager.

    Fail Quiet principle: one alert, then fall back to printed SOP.
    """
    site_name = kwargs.get("site_name", "site")

    if alert_type == "ingestion_failed":
        message = sms_prompts.system_alert_ingestion_failed(site_name, kwargs.get("error", ""))
    elif alert_type == "prediction_blocked_data_quality":
        message = sms_prompts.system_alert_prediction_blocked(
            site_name=site_name,
            run_date=kwargs.get("run_date", ""),
            reasons=kwargs.get("reasons", []),
        )
    elif alert_type == "sms_failed":
        message = sms_prompts.system_alert_sms_failed(site_name)
    else:
        message = f"Autopilot Alert ({alert_type}): Check system status."

    return send_to_manager(site_id, message)


# ============================================================
# Message Builder
# ============================================================


def _build_message(
    action_type: str,
    details: dict,
    staff_names: dict,
) -> Optional[str]:
    """
    Map an action_type to its SMS prompt template.

    Uses the sms_prompts module for all message formatting.
    """
    if action_type == "PRE_RUSH_REMINDER":
        return sms_prompts.pre_rush_reminder(
            rush_start=details.get("rush_start", ""),
            rush_end=details.get("rush_end", ""),
            predicted_drinks=details.get("predicted_drinks", 0),
            confidence_label=details.get("confidence_label", "medium"),
            switch_3p_time=details.get("switch_3p_time"),
        )

    elif action_type == "SWITCH_TO_3P":
        return sms_prompts.switch_to_3p(
            staff_names=staff_names,
            rush_start=details.get("rush_start", ""),
        )

    elif action_type == "WALLY_START":
        return sms_prompts.wally_start(
            volume_litres=details.get("volume_litres", 0),
            split=details.get("split", {}),
            staff_name=staff_names.get("P2", "P2"),
        )

    elif action_type == "P1_TO_SHOTS":
        return sms_prompts.p1_to_shots()

    elif action_type == "DELIVERY_OVERRIDE" or action_type == "DELIVERY_PRIORITY":
        return sms_prompts.delivery_override(
            staff_name=staff_names.get("P3", "P3"),
        )

    elif action_type == "RUSH_END":
        return sms_prompts.rush_easing()

    elif action_type == "END_OF_DAY_FEEDBACK":
        return sms_prompts.end_of_day_feedback()

    elif action_type == "TOMORROW_PLAN_SEND":
        # This is handled by send_tomorrow_plan_sms() directly
        return details.get("message", "Tomorrow Plan ready. Print and place on bench.")

    else:
        # Fallback: use the message from action_details
        return details.get("message")


# ============================================================
# Intelligence Digest
# ============================================================


def dispatch_intelligence_digest(site_id: str, insights: list[dict]) -> list[dict]:
    """Send top insights as a morning SMS digest to manager."""
    if not insights:
        return []

    top = [i for i in insights if i.get("severity") in ("opportunity", "warning")][:3]
    if not top:
        return []

    lines = ["Autopilot Intelligence:"]
    for insight in top:
        icon = "!" if insight.get("severity") == "warning" else ">"
        lines.append(f"{icon} {insight.get('title', '')}")

    lines.append("Ask me for details in chat.")
    message = "\n".join(lines)
    return send_to_manager(site_id, message)
