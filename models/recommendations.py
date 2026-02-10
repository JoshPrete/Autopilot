"""
Clubhouse Autopilot v1.2 - Recommendation Engine
State machine logic + action generation (Spec Sections 2-3)

Converts predictions and rush windows into concrete, timed
recommendations for each role (P1/P2/P3/MANAGER). This is the
brain that turns forecasts into the action items on the
Tomorrow Plan and real-time SMS nudges.
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from config.constants import (
    ROLES,
    STATES,
    TRANSITION_2P_TO_3P,
    TRANSITION_WALLY_ACTIVATE,
)
from data.processing import calculate_wally_volume
from data.storage import get_contacts_by_role, store_recommendation

logger = logging.getLogger("autopilot.recommendations")


# ============================================================
# State Machine (Spec Section 3.1)
# ============================================================


class StateMachine:
    """
    Workflow state machine managing transitions between staffing modes.

    States (Section 3.1):
        S1P_SURVIVAL  - 1 person, solo cycles everything
        S2P_STANDARD  - 2 people, baseline mode
        S2P_BUSY      - 2 people, building pressure
        S3P_RUSH      - 3 people, rush execution
        S4P_STANDARD  - 4+ people, high consistency
    """

    def __init__(self, initial_state: str = "S2P_STANDARD"):
        if initial_state not in STATES:
            raise ValueError(f"Invalid state: {initial_state}. Must be one of {list(STATES.keys())}")
        self.current_state = initial_state
        self.history: list[dict] = []

    @property
    def staff_count(self) -> int:
        return STATES[self.current_state]["staff"]

    @property
    def purpose(self) -> str:
        return STATES[self.current_state]["purpose"]

    def transition(self, new_state: str, reason: str = "") -> dict:
        """
        Transition to a new state. Records history.

        Returns dict describing the transition for logging/SMS.
        """
        if new_state not in STATES:
            raise ValueError(f"Invalid target state: {new_state}")

        old_state = self.current_state
        self.current_state = new_state

        record = {
            "from_state": old_state,
            "to_state": new_state,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            "new_staff_count": STATES[new_state]["staff"],
        }
        self.history.append(record)

        logger.info(
            "State transition: %s -> %s (reason: %s)",
            old_state, new_state, reason,
        )
        return record

    def resolve_state_from_staff(self, staff_count: int, is_rush: bool = False) -> str:
        """Determine the correct state given staff count and rush status."""
        if staff_count <= 1:
            return "S1P_SURVIVAL"
        elif staff_count == 2:
            return "S2P_BUSY" if is_rush else "S2P_STANDARD"
        elif staff_count == 3:
            return "S3P_RUSH"
        return "S4P_STANDARD"


# ============================================================
# Recommendation Generation
# ============================================================


def generate_rush_recommendations(
    site_id: str,
    prediction_id: str,
    rush_window: dict,
    staff_names: dict = None,
) -> list[dict]:
    """
    Generate all recommendations for a single rush window.

    Creates timed actions per Spec Section 3.3:
    1. WALLY_START - P2 starts Wally cycling (T-12 min)
    2. SWITCH_TO_3P - Switch to 3P workflow (T-7 min)
    3. P3_PREP - P3 pre-stages cups (T-7 min)
    4. DELIVERY_PRIORITY - P3 delivery focus (rush start)
    5. PRE_RUSH_REMINDER - SMS alert (T-15 min)

    Args:
        site_id: Site UUID
        prediction_id: FK to predictions table
        rush_window: Dict from RushWindow.to_dict()
        staff_names: Optional dict mapping role -> name (e.g. {"P1": "Sarah"})

    Returns:
        List of recommendation dicts (also stored to DB)
    """
    if staff_names is None:
        staff_names = {}

    rush_start = datetime.fromisoformat(rush_window["start"])
    rush_end = datetime.fromisoformat(rush_window["end"])
    predicted_drinks = rush_window["predicted_drinks"]
    wally_litres = rush_window["wally_volume_litres"]
    wally_split = rush_window["wally_split"]
    confidence_label = rush_window["confidence_label"]

    recommendations = []

    # 1. PRE_RUSH_REMINDER (T-15 min) -> MANAGER
    reminder_time = rush_start - timedelta(minutes=15)
    recommendations.append(_create_recommendation(
        prediction_id=prediction_id,
        site_id=site_id,
        action_type="PRE_RUSH_REMINDER",
        action_timing=reminder_time,
        owner_role="MANAGER",
        action_details={
            "message": (
                f"Rush in 15 mins. "
                f"{predicted_drinks} drinks predicted "
                f"({confidence_label} confidence)."
            ),
            "rush_start": rush_start.isoformat(),
            "rush_end": rush_end.isoformat(),
            "predicted_drinks": predicted_drinks,
        },
    ))

    # 2. WALLY_START (T-12 min) -> P2
    wally_time = rush_start - timedelta(minutes=12)
    p2_name = staff_names.get("P2", "P2")
    recommendations.append(_create_recommendation(
        prediction_id=prediction_id,
        site_id=site_id,
        action_type="WALLY_START",
        action_timing=wally_time,
        owner_role="P2",
        action_details={
            "message": f"{p2_name}: Start Wally cycling",
            "volume_litres": wally_litres,
            "split": wally_split,
            "mode": "CYCLING",
        },
    ))

    # 3. SWITCH_TO_3P (T-7 min) -> MANAGER
    switch_time = rush_start - timedelta(
        minutes=TRANSITION_2P_TO_3P["lead_time_minutes"]
    )
    recommendations.append(_create_recommendation(
        prediction_id=prediction_id,
        site_id=site_id,
        action_type="SWITCH_TO_3P",
        action_timing=switch_time,
        owner_role="MANAGER",
        action_details={
            "message": "Switch to 3P workflow NOW",
            "roles": {
                "P1": {
                    "name": staff_names.get("P1", "P1"),
                    "task": "Front / Orders",
                },
                "P2": {
                    "name": staff_names.get("P2", "P2"),
                    "task": "Shots + Milk (owns Wally)",
                },
                "P3": {
                    "name": staff_names.get("P3", "P3"),
                    "task": "Delivery + Prep",
                },
            },
            "rush_start": rush_start.isoformat(),
        },
    ))

    # 4. P3_PREP (same as 3P switch) -> P3
    recommendations.append(_create_recommendation(
        prediction_id=prediction_id,
        site_id=site_id,
        action_type="P3_PREP",
        action_timing=switch_time,
        owner_role="P3",
        action_details={
            "message": f"{staff_names.get('P3', 'P3')}: Pre-stage 30 cups at bar",
            "cups_to_stage": 30,
        },
    ))

    # 5. DELIVERY_PRIORITY (rush start) -> P3
    recommendations.append(_create_recommendation(
        prediction_id=prediction_id,
        site_id=site_id,
        action_type="DELIVERY_PRIORITY",
        action_timing=rush_start,
        owner_role="P3",
        action_details={
            "message": "Delivery priority NOW. Deliver as soon as ready.",
            "rule": "Delivery > Prep",
        },
    ))

    # 6. RUSH_END (rush end time) -> MANAGER
    recommendations.append(_create_recommendation(
        prediction_id=prediction_id,
        site_id=site_id,
        action_type="RUSH_END",
        action_timing=rush_end,
        owner_role="MANAGER",
        action_details={
            "message": "Rush easing. Return to 2P baseline. P3 can transition out.",
        },
    ))

    logger.info(
        "Generated %d recommendations for rush %s-%s (%d drinks)",
        len(recommendations),
        rush_start.strftime("%H:%M"),
        rush_end.strftime("%H:%M"),
        predicted_drinks,
    )

    return recommendations


def _create_recommendation(
    prediction_id: str,
    site_id: str,
    action_type: str,
    action_timing: datetime,
    owner_role: str,
    action_details: dict,
) -> dict:
    """Create and store a single recommendation."""
    rec_id = store_recommendation(
        prediction_id=prediction_id,
        site_id=site_id,
        action_type=action_type,
        action_timing=action_timing,
        owner_role=owner_role,
        action_details=action_details,
    )

    return {
        "rec_id": rec_id,
        "action_type": action_type,
        "action_timing": action_timing.isoformat(),
        "owner_role": owner_role,
        "action_details": action_details,
    }


# ============================================================
# Full Day Recommendation Pipeline
# ============================================================


def generate_daily_recommendations(
    site_id: str,
    prediction_id: str,
    prediction: dict,
    staff_names: dict = None,
) -> list[dict]:
    """
    Generate all recommendations for an entire day's prediction.

    Iterates through all rush windows and generates timed actions
    for each. Also adds the Tomorrow Plan send and end-of-day
    feedback request.

    Args:
        site_id: Site UUID
        prediction_id: FK to predictions table
        prediction: Full prediction dict from generate_prediction()
        staff_names: Optional role -> name mapping

    Returns:
        Complete list of all recommendations for the day
    """
    all_recs = []
    rush_windows = prediction.get("rush_windows", [])

    # Generate recommendations for each rush window
    for i, rush in enumerate(rush_windows):
        recs = generate_rush_recommendations(
            site_id=site_id,
            prediction_id=prediction_id,
            rush_window=rush,
            staff_names=staff_names,
        )
        all_recs.extend(recs)

    # Add TOMORROW_PLAN_SEND (6pm today) -> MANAGER
    forecast_date_str = prediction.get("forecast", {}).get("forecast_date")
    if forecast_date_str:
        forecast_date = date.fromisoformat(forecast_date_str)
        plan_send_time = datetime(
            forecast_date.year, forecast_date.month, forecast_date.day, 18, 0
        ) - timedelta(days=1)

        all_recs.append(_create_recommendation(
            prediction_id=prediction_id,
            site_id=site_id,
            action_type="TOMORROW_PLAN_SEND",
            action_timing=plan_send_time,
            owner_role="MANAGER",
            action_details={
                "message": "Tomorrow Plan ready. Print and place on bench.",
                "total_drinks": prediction.get("total_predicted_drinks", 0),
                "rush_count": prediction.get("rush_count", 0),
            },
        ))

    # Add END_OF_DAY_FEEDBACK (3pm) -> MANAGER
    if forecast_date_str:
        forecast_date = date.fromisoformat(forecast_date_str)
        feedback_time = datetime(
            forecast_date.year, forecast_date.month, forecast_date.day, 15, 0
        )

        all_recs.append(_create_recommendation(
            prediction_id=prediction_id,
            site_id=site_id,
            action_type="END_OF_DAY_FEEDBACK",
            action_timing=feedback_time,
            owner_role="MANAGER",
            action_details={
                "message": (
                    "How was today's plan? "
                    "Reply 2 numbers: Rush timing (1-5), Helpfulness (1-5). "
                    'Example: "5 4"'
                ),
            },
        ))

    logger.info(
        "Generated %d total recommendations for %s (%d rush windows)",
        len(all_recs), forecast_date_str, len(rush_windows),
    )

    return all_recs


# ============================================================
# Real-Time Recommendation from Queue Signals
# ============================================================


def generate_realtime_recommendations(
    site_id: str,
    prediction_id: str,
    triggered_transitions: list[dict],
    staff_names: dict = None,
) -> list[dict]:
    """
    Convert triggered state transitions into immediate SMS recommendations.

    Called by the real-time monitoring loop when queue signals
    trigger state transitions (from workload.evaluate_transition_signals).

    Args:
        site_id: Site UUID
        prediction_id: FK to today's prediction
        triggered_transitions: Output from evaluate_transition_signals()
        staff_names: Optional role -> name mapping

    Returns:
        List of recommendation dicts for immediate SMS dispatch
    """
    if staff_names is None:
        staff_names = {}

    now = datetime.utcnow()
    recs = []

    for transition in triggered_transitions:
        t_type = transition["transition"]
        owner = transition["owner_role"]
        action = transition["action"]

        details = {
            "message": action,
            "conditions": transition["conditions_met"],
            "triggered_at": now.isoformat(),
        }

        # Enrich specific transitions with role names
        if t_type == "SWITCH_TO_3P":
            details["roles"] = {
                "P1": {"name": staff_names.get("P1", "P1"), "task": "Front"},
                "P2": {"name": staff_names.get("P2", "P2"), "task": "Shots+Milk"},
                "P3": {"name": staff_names.get("P3", "P3"), "task": "Delivery"},
            }
        elif t_type == "WALLY_START":
            details["owner_name"] = staff_names.get("P2", "P2")

        rec = _create_recommendation(
            prediction_id=prediction_id,
            site_id=site_id,
            action_type=t_type,
            action_timing=now,
            owner_role=owner,
            action_details=details,
        )
        recs.append(rec)

    if recs:
        logger.info("Generated %d real-time recommendations", len(recs))

    return recs


# ============================================================
# Pre-Rush Checklist Generation
# ============================================================


def generate_pre_rush_checklist(rush_window: dict) -> list[str]:
    """
    Generate the pre-rush checklist items for the Tomorrow Plan.

    These are the physical prep tasks that appear on the printed
    sheet (Spec Section 8.1).
    """
    checklist = [
        "Grind 2kg beans",
        "Check Bar 2 steam wand",
    ]

    wally_split = rush_window.get("wally_split", {})
    oat_litres = wally_split.get("oat", 2)

    # Restock items based on predicted volume
    restock_items = []
    if oat_litres >= 2:
        cartons = max(4, oat_litres * 2)
        restock_items.append(f"oat milk ({cartons} cartons)")
    restock_items.append("syrups")

    if restock_items:
        checklist.append(f"Restock: {', '.join(restock_items)}")

    checklist.extend([
        "Clear dish station",
        "Brief team (use this sheet)",
    ])

    return checklist


# ============================================================
# Role Assignment
# ============================================================


def get_role_assignments(
    staff_names: dict,
    state: str,
) -> list[dict]:
    """
    Generate role assignments for a given state and staff roster.

    Maps staff names to their P1/P2/P3 roles with primary and
    secondary responsibilities from Spec Section 2.2.

    Args:
        staff_names: Dict mapping role -> name (e.g. {"P1": "Sarah"})
        state: Current state machine state

    Returns:
        List of role assignment dicts
    """
    state_info = STATES.get(state, STATES["S2P_STANDARD"])
    staff_needed = state_info["staff"]
    assignments = []

    role_order = ["P1", "P2", "P3", "MANAGER"]

    for role in role_order[:staff_needed]:
        role_info = ROLES.get(role, {})
        name = staff_names.get(role, role)

        assignments.append({
            "role": role,
            "name": name,
            "primary": role_info.get("primary", ""),
            "secondary": role_info.get("secondary", ""),
        })

    return assignments


# ============================================================
# Wally Recommendation Details
# ============================================================


def build_wally_plan(rush_windows: list[dict]) -> dict:
    """
    Build a complete Wally plan across all rush windows.

    Calculates total milk needed, batch timing, and per-rush
    volume breakdowns. Used by the Tomorrow Plan generator.
    """
    if not rush_windows:
        return {"mode": "OFF", "batches": []}

    batches = []
    total_litres = 0

    for i, rush in enumerate(rush_windows):
        volume = rush.get("wally_volume_litres", 0)
        split = rush.get("wally_split", {})
        wally_start = rush.get("wally_start_time", rush["start"])

        batches.append({
            "batch_number": i + 1,
            "start_time": wally_start,
            "volume_litres": volume,
            "split": split,
            "rush_window": f"{rush['start']} - {rush['end']}",
        })
        total_litres += volume

    # Determine mode based on rush count
    if len(rush_windows) >= 2:
        mode = "CYCLING"
    elif rush_windows[0].get("duration_minutes", 0) > 30:
        mode = "CYCLING"
    else:
        mode = "ON_DEMAND"

    return {
        "mode": mode,
        "total_litres": total_litres,
        "batches": batches,
        "owner": "P2",
    }
