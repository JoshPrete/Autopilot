"""
Structured operator knowledge parsing for chat-confirmed business rules.
"""

from __future__ import annotations

import re
from datetime import datetime

from analysis.sale_understanding import build_sale_profile_payload

WEEKDAY_ORDER = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]

WEEKDAY_LOOKUP = {
    "mon": "monday",
    "monday": "monday",
    "tue": "tuesday",
    "tues": "tuesday",
    "tuesday": "tuesday",
    "wed": "wednesday",
    "wednesday": "wednesday",
    "thu": "thursday",
    "thur": "thursday",
    "thurs": "thursday",
    "thursday": "thursday",
    "fri": "friday",
    "friday": "friday",
    "sat": "saturday",
    "saturday": "saturday",
    "sun": "sunday",
    "sunday": "sunday",
}

DAY_PATTERN = (
    r"mon(?:day)?|tue(?:s|sday)?|wed(?:nesday)?|thu(?:r|rs|rsday)?|"
    r"fri(?:day)?|sat(?:urday)?|sun(?:day)?"
)

CONFIRM_MESSAGES = {
    "confirm",
    "confirmed",
    "save",
    "save it",
    "yes save",
    "yes confirm",
    "approve",
    "approved",
}

REJECT_MESSAGES = {
    "reject",
    "discard",
    "cancel",
    "no save",
    "don't save",
    "do not save",
}


def is_confirmation_message(message: str) -> bool:
    normalized = _normalize_message(message)
    return normalized in CONFIRM_MESSAGES


def is_rejection_message(message: str) -> bool:
    normalized = _normalize_message(message)
    return normalized in REJECT_MESSAGES


def parse_operator_rule_message(message: str) -> dict | None:
    normalized = " ".join((message or "").strip().split())
    if not normalized or "?" in normalized:
        return None

    for parser in (
        _parse_staffing_constraint,
        _parse_ordering_schedule,
        _parse_delivery_schedule,
        _parse_storage_rule,
        _parse_recipe_definition,
    ):
        proposal = parser(normalized)
        if proposal:
            return proposal
    return None


def summarize_operator_rule(rule: dict) -> str:
    rule_type = (rule or {}).get("rule_type")
    payload = (rule or {}).get("payload") or {}

    if rule_type == "delivery_schedule":
        subject = payload.get("subject") or "Delivery"
        days = ", ".join(_title_day(day) for day in payload.get("days", []))
        return f"{subject}: delivery on {days}" if days else f"{subject}: delivery schedule"

    if rule_type == "ordering_schedule":
        subject = payload.get("subject") or "Ordering"
        cutoff_day = _title_day(payload.get("cutoff_day"))
        cutoff_time = _display_time(payload.get("cutoff_time"))
        delivery_day = _title_day(payload.get("delivery_day"))
        if cutoff_day and cutoff_time and delivery_day:
            return f"{subject}: order by {cutoff_time} {cutoff_day} for {delivery_day} delivery"
        return f"{subject}: ordering schedule"

    if rule_type == "storage_rule":
        subject = payload.get("subject") or "Item"
        location = payload.get("storage_location") or "specified storage"
        condition = payload.get("condition")
        if condition:
            return f"{subject}: store in {location} until {condition}"
        return f"{subject}: store in {location}"

    if rule_type == "recipe_definition":
        trigger_item_name = payload.get("trigger_item_name") or "Recipe"
        components = payload.get("components") or []
        component_summary = "; ".join(
            _summarize_component(component) for component in components[:4]
        )
        if len(components) > 4:
            component_summary += f"; +{len(components) - 4} more"
        return (
            f"{trigger_item_name}: uses {component_summary}"
            if component_summary
            else trigger_item_name
        )

    if rule_type == "staffing_constraint":
        day = _title_day(payload.get("day_of_week")) or "Specified day"
        daypart = payload.get("daypart") or "all_day"
        daypart_text = f" {daypart}" if daypart != "all_day" else ""
        parts = []
        if payload.get("min_staff") is not None:
            parts.append(f"minimum {int(payload['min_staff'])} staff")
        if payload.get("requires_senior"):
            parts.append("senior coverage required")
        if payload.get("disallow_role_alone"):
            parts.append(f"do not leave {payload['disallow_role_alone']} alone")
        detail = ", ".join(parts) if parts else "staffing constraint"
        return f"{day}{daypart_text}: {detail}"

    return (rule or {}).get("rule_name") or "Unlabelled operator rule"


def build_rule_capture_response(rule: dict) -> str:
    summary = summarize_operator_rule(rule)
    rule_label = _rule_type_label(rule.get("rule_type"))
    return (
        "Proposed operating rule captured.\n\n"
        f"- Type: {rule_label}\n"
        f"- Parsed: {summary}\n\n"
        "Reply `confirm` to save it or `reject` to discard it."
    )


def build_rule_saved_response(rule: dict) -> str:
    return (
        "Operating rule saved.\n\n"
        f"- {summarize_operator_rule(rule)}\n\n"
        "This rule is now available to future chat reasoning."
    )


def build_rule_rejected_response(rule: dict) -> str:
    return f"Pending rule discarded.\n\n- {summarize_operator_rule(rule)}"


def _parse_delivery_schedule(message: str) -> dict | None:
    match = re.search(
        r"^(?P<subject>.+?)\s+deliver(?:y|ies)(?:\s+days?)?\s+(?:is|are|=|on|arrive|arrives)\s+(?P<days>.+)$",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None

    days = _extract_weekdays(match.group("days"))
    if not days:
        return None

    subject = _clean_subject(match.group("subject"))
    return {
        "rule_type": "delivery_schedule",
        "rule_name": f"{subject} delivery schedule",
        "confidence": 0.96,
        "payload": {
            "subject": subject,
            "days": days,
        },
    }


def _parse_staffing_constraint(message: str) -> dict | None:
    patterns = [
        re.compile(
            rf"^do not roster (?P<role>.+?) alone on (?P<day>{DAY_PATTERN})(?: (?P<daypart>open|close))?$",
            re.IGNORECASE,
        ),
        re.compile(
            rf"^(?P<day>{DAY_PATTERN})(?: (?P<daypart>open|close))? (?:needs|requires) (?P<count>\d+) staff$",
            re.IGNORECASE,
        ),
        re.compile(
            rf"^(?P<day>{DAY_PATTERN})(?: (?P<daypart>open|close))? (?:needs|requires) senior staff$",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        match = pattern.search(message)
        if not match:
            continue

        day_of_week = _normalize_weekday(match.group("day"))
        daypart = (match.groupdict().get("daypart") or "all_day").lower()
        if daypart not in {"open", "close", "all_day"}:
            daypart = "all_day"
        if not day_of_week:
            return None

        role = match.groupdict().get("role")
        count = match.groupdict().get("count")
        disallow_role_alone = _normalize_role_label(role) if role else None
        requires_senior = "senior staff" in message.lower() or (
            disallow_role_alone is not None and "junior" in disallow_role_alone
        )
        min_staff = int(count) if count is not None else None
        if disallow_role_alone:
            min_staff = max(2, int(min_staff or 0))

        return {
            "rule_type": "staffing_constraint",
            "rule_name": f"{_title_day(day_of_week)} {daypart} staffing constraint".strip(),
            "confidence": 0.95,
            "payload": {
                "day_of_week": day_of_week,
                "daypart": daypart,
                "min_staff": min_staff,
                "requires_senior": requires_senior,
                "disallow_role_alone": disallow_role_alone,
            },
        }

    return None


def _parse_ordering_schedule(message: str) -> dict | None:
    patterns = [
        re.compile(
            r"^(?:we\s+place\s+)?(?P<subject>.+?)\s+orders?\s+(?:by|before)\s+"
            r"(?P<time>\d{1,2}(?::\d{2})?\s*(?:am|pm))\s+"
            rf"(?P<cutoff_day>{DAY_PATTERN})"
            r"(?:\s+for\s+|\s+for\s+the\s+)"
            rf"(?P<delivery_day>{DAY_PATTERN})"
            r"\s+delivery$",
            re.IGNORECASE,
        ),
        re.compile(
            r"^(?P<subject>.+?)\s+(?:must\s+be\s+ordered|should\s+be\s+ordered|needs\s+to\s+be\s+ordered)\s+by\s+"
            r"(?P<time>\d{1,2}(?::\d{2})?\s*(?:am|pm))\s+"
            rf"(?P<cutoff_day>{DAY_PATTERN})"
            r"\s+for\s+"
            rf"(?P<delivery_day>{DAY_PATTERN})"
            r"\s+delivery$",
            re.IGNORECASE,
        ),
    ]

    for pattern in patterns:
        match = pattern.search(message)
        if not match:
            continue

        subject = _clean_subject(match.group("subject"))
        cutoff_day = _normalize_weekday(match.group("cutoff_day"))
        delivery_day = _normalize_weekday(match.group("delivery_day"))
        cutoff_time = _normalize_time(match.group("time"))
        if not (subject and cutoff_day and delivery_day and cutoff_time):
            return None

        return {
            "rule_type": "ordering_schedule",
            "rule_name": f"{subject} ordering schedule",
            "confidence": 0.95,
            "payload": {
                "subject": subject,
                "cutoff_day": cutoff_day,
                "cutoff_time": cutoff_time,
                "delivery_day": delivery_day,
            },
        }

    return None


def _parse_storage_rule(message: str) -> dict | None:
    match = re.search(
        r"^(?P<subject>.+?)\s+(?:is|are)\s+stored\s+in\s+(?P<location>.+?)(?:\s+until\s+(?P<condition>.+))?$",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None

    subject = _clean_subject(match.group("subject"))
    location = (match.group("location") or "").strip(" .")
    condition = (match.group("condition") or "").strip(" .") or None
    if not subject or not location:
        return None

    return {
        "rule_type": "storage_rule",
        "rule_name": f"{subject} storage rule",
        "confidence": 0.93,
        "payload": {
            "subject": subject,
            "storage_location": location,
            "condition": condition,
        },
    }


def _parse_recipe_definition(message: str) -> dict | None:
    match = re.search(
        r"^(?:a|an|the)\s+(?P<trigger>.+?)\s+uses\s+(?P<components>.+)$",
        message,
        re.IGNORECASE,
    )
    if not match:
        match = re.search(r"^(?P<trigger>.+?)\s+uses\s+(?P<components>.+)$", message, re.IGNORECASE)
    if not match:
        return None

    trigger_item_name = (match.group("trigger") or "").strip(" .")
    components_raw = (match.group("components") or "").strip(" .")
    if not trigger_item_name or not components_raw:
        return None

    components = []
    parts = re.split(r",|\band\b", components_raw, flags=re.IGNORECASE)
    for part in parts:
        component = _parse_recipe_component(part)
        if component:
            components.append(component)

    if not components:
        return None

    return {
        "rule_type": "recipe_definition",
        "rule_name": f"{trigger_item_name} recipe definition",
        "confidence": 0.9,
        "payload": {
            "trigger_item_name": trigger_item_name,
            "sale_profile": build_sale_profile_payload(trigger_item_name),
            "components": components,
        },
    }


def _parse_recipe_component(raw_component: str) -> dict | None:
    chunk = (raw_component or "").strip(" .")
    if not chunk:
        return None

    match = re.match(r"^(?P<qty>\d+(?:\.\d+)?)(?P<unit>[A-Za-z]+)?\s+(?P<name>.+)$", chunk)
    if not match:
        return None

    quantity = float(match.group("qty"))
    unit = (match.group("unit") or "units").lower()
    item_name = (match.group("name") or "").strip(" .")
    if not item_name:
        return None

    return {
        "item_name": item_name,
        "quantity": quantity,
        "unit": unit,
    }


def _extract_weekdays(raw_days: str) -> list[str]:
    found = []
    haystack = f" {raw_days.lower()} "
    for token, normalized in WEEKDAY_LOOKUP.items():
        if re.search(rf"\b{re.escape(token)}\b", haystack):
            found.append(normalized)

    ordered = []
    for day in WEEKDAY_ORDER:
        if day in found and day not in ordered:
            ordered.append(day)
    return ordered


def _normalize_weekday(raw_day: str) -> str | None:
    token = (raw_day or "").strip(" .").lower()
    return WEEKDAY_LOOKUP.get(token)


def _normalize_time(raw_time: str) -> str | None:
    token = " ".join((raw_time or "").strip().lower().split())
    if not token:
        return None
    try:
        if ":" in token:
            value = datetime.strptime(token, "%I:%M %p")
        else:
            value = datetime.strptime(token, "%I %p")
        return value.strftime("%H:%M")
    except ValueError:
        return None


def _normalize_message(message: str) -> str:
    return " ".join((message or "").strip().lower().split())


def _clean_subject(subject: str) -> str:
    cleaned = re.sub(r"^(the|our|we)\s+", "", (subject or "").strip(), flags=re.IGNORECASE)
    return cleaned.strip(" .")


def _normalize_role_label(raw_role: str | None) -> str | None:
    token = (raw_role or "").strip(" .").lower()
    if not token:
        return None
    singular_map = {"juniors": "junior", "seniors": "senior", "baristas": "barista"}
    return singular_map.get(token, token)


def _rule_type_label(rule_type: str | None) -> str:
    labels = {
        "delivery_schedule": "Delivery schedule",
        "ordering_schedule": "Ordering schedule",
        "storage_rule": "Storage rule",
        "recipe_definition": "Recipe definition",
        "staffing_constraint": "Staffing constraint",
    }
    return labels.get(rule_type or "", "Operator rule")


def _title_day(day: str | None) -> str:
    if not day:
        return ""
    return str(day).strip().capitalize()


def _display_time(raw_time: str | None) -> str:
    if not raw_time:
        return ""
    try:
        value = datetime.strptime(raw_time, "%H:%M")
        return value.strftime("%I:%M%p").lstrip("0").lower()
    except ValueError:
        return str(raw_time)


def _summarize_component(component: dict) -> str:
    quantity = component.get("quantity")
    unit = component.get("unit") or "units"
    name = component.get("item_name") or "component"
    quantity_display = int(quantity) if float(quantity).is_integer() else quantity
    return f"{quantity_display} {unit} {name}".strip()
