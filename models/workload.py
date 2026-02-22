"""
Clubhouse Autopilot v1.2 - Workload Model
High-level workload engine orchestrating data layer calls (Spec Sections 4-5)

Provides the business-logic layer between raw data processing and
the prediction/recommendation engines. Handles:
- Full-day workload analysis with rush window detection
- Wally volume planning across rush windows
- Real-time queue signal evaluation (Section 3.2)
- Baseline comparison for state transitions
"""

import logging
from datetime import date, datetime, timedelta
from typing import Optional

from config.constants import (
    MILK_DRINK_KEYS,
    TRANSITION_2P_TO_3P,
    TRANSITION_DELIVERY_OVERRIDE,
    TRANSITION_P1_TO_SHOTS,
    TRANSITION_RUSH_END,
    TRANSITION_WALLY_ACTIVATE,
)
from config.settings import settings
from data.processing import (
    calculate_wally_volume,
    predict_workload,
)
from data.storage import (
    check_special_events,
    get_avg_workload_per_drink,
    get_recent_pattern,
    get_yoy_pattern,
)

logger = logging.getLogger("autopilot.workload")


# ============================================================
# Rush Window Detection
# ============================================================


class RushWindow:
    """A detected rush period with timing, volume, and actions."""

    def __init__(
        self,
        start: datetime,
        end: datetime,
        predicted_drinks: int,
        predicted_workload: float,
        confidence: float,
        confidence_label: str,
    ):
        self.start = start
        self.end = end
        self.predicted_drinks = predicted_drinks
        self.predicted_workload = predicted_workload
        self.confidence = confidence
        self.confidence_label = confidence_label

    @property
    def duration_minutes(self) -> int:
        return int((self.end - self.start).total_seconds() / 60)

    @property
    def wally_volume(self) -> dict:
        return calculate_wally_volume(self.predicted_drinks)

    @property
    def switch_3p_time(self) -> datetime:
        """T-7 minutes before rush start (Spec Section 3.3)."""
        return self.start - timedelta(minutes=TRANSITION_2P_TO_3P["lead_time_minutes"])

    @property
    def alert_time(self) -> datetime:
        """T-10 minutes before rush start for SMS alert."""
        return self.start - timedelta(minutes=TRANSITION_2P_TO_3P["alert_lead_minutes"])

    @property
    def wally_start_time(self) -> datetime:
        """Wally should start 12 minutes before rush (before 3P switch)."""
        return self.start - timedelta(minutes=12)

    def to_dict(self) -> dict:
        wally = self.wally_volume
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "duration_minutes": self.duration_minutes,
            "predicted_drinks": self.predicted_drinks,
            "predicted_workload": round(self.predicted_workload, 1),
            "confidence": self.confidence,
            "confidence_label": self.confidence_label,
            "switch_3p_time": self.switch_3p_time.isoformat(),
            "alert_time": self.alert_time.isoformat(),
            "wally_start_time": self.wally_start_time.isoformat(),
            "wally_volume_litres": wally["total_litres"],
            "wally_split": wally["split"],
        }


def detect_rush_windows(
    site_id: str,
    target_date: date,
    operating_hours: tuple[int, int] = (5, 16),
) -> list[RushWindow]:
    """
    Scan each hour of the target date for rush conditions.

    For each hour in operating_hours, runs the 4-layer prediction
    and checks if the result exceeds the rush threshold.
    Adjacent rush hours are merged into single windows.

    Args:
        site_id: Site UUID
        target_date: Date to forecast
        operating_hours: (open_hour, close_hour) in 24h format

    Returns:
        List of RushWindow objects, sorted by start time
    """
    day_name = target_date.strftime("%A")
    event_multiplier = check_special_events(site_id, target_date)

    hourly_predictions = []
    open_hour, close_hour = operating_hours

    for hour in range(open_hour, close_hour):
        # Python weekday: 0=Mon...6=Sun. Postgres DOW: 0=Sun...6=Sat.
        py_dow = target_date.weekday()
        pg_dow = (py_dow + 1) % 7

        recent = get_recent_pattern(site_id, pg_dow, hour)
        yoy = get_yoy_pattern(site_id, target_date)

        prediction = predict_workload(
            recent_values=recent,
            yoy_values=yoy,
            dow_name=day_name,
            event_multiplier=event_multiplier,
        )

        hourly_predictions.append(
            {
                "hour": hour,
                "prediction": prediction,
            }
        )

    # Find consecutive rush hours and merge into windows
    rush_windows = []
    current_window_hours = []

    for hp in hourly_predictions:
        if hp["prediction"]["is_rush"]:
            current_window_hours.append(hp)
        else:
            if current_window_hours:
                rush_windows.append(_merge_rush_hours(target_date, current_window_hours))
                current_window_hours = []

    # Don't forget trailing window
    if current_window_hours:
        rush_windows.append(_merge_rush_hours(target_date, current_window_hours))

    logger.info(
        "Detected %d rush windows for %s (event_factor=%.2f)",
        len(rush_windows),
        target_date,
        event_multiplier,
    )
    return rush_windows


def _merge_rush_hours(target_date: date, hours: list[dict]) -> RushWindow:
    """Merge consecutive rush-flagged hours into a single RushWindow."""
    first_hour = hours[0]["hour"]
    last_hour = hours[-1]["hour"]

    start = datetime(target_date.year, target_date.month, target_date.day, first_hour, 0)
    end = datetime(target_date.year, target_date.month, target_date.day, last_hour + 1, 0)

    # Sum predictions across the window
    total_prediction = sum(h["prediction"]["final_prediction"] for h in hours)
    avg_confidence = sum(h["prediction"]["confidence"] for h in hours) / len(hours)

    # Estimate drinks from workload (rough: avg ~3 units per drink)
    estimated_drinks = int(total_prediction / 3.0)

    # Use worst confidence label
    min_confidence = min(h["prediction"]["confidence"] for h in hours)
    if min_confidence >= 0.85:
        label = "high"
    elif min_confidence >= 0.70:
        label = "medium"
    else:
        label = "low"

    return RushWindow(
        start=start,
        end=end,
        predicted_drinks=estimated_drinks,
        predicted_workload=total_prediction,
        confidence=round(avg_confidence, 2),
        confidence_label=label,
    )


# ============================================================
# Full-Day Forecast
# ============================================================


def generate_daily_forecast(
    site_id: str,
    target_date: date,
    staff_scheduled: int = None,
    operating_hours: tuple[int, int] = (5, 16),
) -> dict:
    """
    Generate a complete daily forecast for the Tomorrow Plan.

    Combines:
    - Hourly predictions across the day
    - Rush window detection with timing
    - Wally volume calculations per rush
    - Staff mode recommendation based on staff count

    This is the main entry point used by the daily_autopilot script
    and the Tomorrow Plan generator.
    """
    day_name = target_date.strftime("%A")
    event_multiplier = check_special_events(site_id, target_date)

    # Detect rush windows
    rush_windows = detect_rush_windows(site_id, target_date, operating_hours)

    # Build hourly breakdown for the full day
    # predict_workload returns the avg workload per 15-min interval;
    # multiply by 4 to get the full hourly total.
    INTERVALS_PER_HOUR = 4
    open_hour, close_hour = operating_hours
    hourly = []
    total_day_workload = 0.0

    for hour in range(open_hour, close_hour):
        py_dow = target_date.weekday()
        pg_dow = (py_dow + 1) % 7

        recent = get_recent_pattern(site_id, pg_dow, hour)
        yoy = get_yoy_pattern(site_id, target_date)

        prediction = predict_workload(
            recent_values=recent,
            yoy_values=yoy,
            dow_name=day_name,
            event_multiplier=event_multiplier,
        )

        hourly_workload = prediction["final_prediction"] * INTERVALS_PER_HOUR
        total_day_workload += hourly_workload
        hourly.append(
            {
                "hour": hour,
                "hour_label": f"{hour}:00",
                "predicted_workload": round(hourly_workload, 1),
                "is_rush": prediction["is_rush"],
                "confidence": prediction["confidence"],
            }
        )

    # Estimate drinks using observed workload-per-drink ratio from recent data
    wu_per_drink = get_avg_workload_per_drink(site_id)
    total_drinks = int(total_day_workload / wu_per_drink)

    # Determine staffing mode from scheduled count
    if staff_scheduled:
        staffing_mode = _resolve_staffing_mode(staff_scheduled)
    else:
        staffing_mode = None

    forecast = {
        "site_id": site_id,
        "forecast_date": target_date.isoformat(),
        "day_name": day_name,
        "event_multiplier": event_multiplier,
        "total_predicted_workload": round(total_day_workload, 1),
        "total_predicted_drinks": total_drinks,
        "staff_scheduled": staff_scheduled,
        "staffing_mode": staffing_mode,
        "rush_windows": [rw.to_dict() for rw in rush_windows],
        "rush_count": len(rush_windows),
        "hourly": hourly,
    }

    logger.info(
        "Daily forecast for %s: %d drinks, %d rush windows, confidence avg %.0f%%",
        target_date,
        total_drinks,
        len(rush_windows),
        sum(h["confidence"] for h in hourly) / max(len(hourly), 1) * 100,
    )

    return forecast


def _resolve_staffing_mode(staff_count: int) -> str:
    """Map staff count to staffing mode label (Spec Section 2.3)."""
    if staff_count <= 1:
        return "1P"
    elif staff_count == 2:
        return "2P"
    elif staff_count == 3:
        return "3P"
    return "4P"


# ============================================================
# Real-Time Queue Signals (Section 3.2)
# ============================================================


class QueueSignals:
    """
    Snapshot of current queue conditions for state transition evaluation.

    Primary signals (always available):
        orders_per_5min - from Square Orders API
        workload_units_per_15min - calculated from orders
        staff_scheduled - from Deputy or manual

    Secondary (if available):
        items_in_progress - from Square KDS
        items_completed - from Square KDS

    Fallback (manual toggles):
        bar2_open, delivery_stacking, milk_queue_high
    """

    def __init__(
        self,
        orders_per_5min: int = 0,
        workload_units_per_15min: float = 0.0,
        staff_on_floor: int = 2,
        items_in_progress: Optional[int] = None,
        items_completed: Optional[int] = None,
        milk_drinks_queued: int = 0,
        orders_waiting: int = 0,
        baseline_workload: float = 0.0,
        minutes_below_baseline: int = 0,
        bar2_open: bool = False,
        delivery_stacking: bool = False,
        milk_queue_high: bool = False,
    ):
        self.orders_per_5min = orders_per_5min
        self.workload_units_per_15min = workload_units_per_15min
        self.staff_on_floor = staff_on_floor
        self.items_in_progress = items_in_progress
        self.items_completed = items_completed
        self.milk_drinks_queued = milk_drinks_queued
        self.orders_waiting = orders_waiting
        self.baseline_workload = baseline_workload
        self.minutes_below_baseline = minutes_below_baseline
        self.bar2_open = bar2_open
        self.delivery_stacking = delivery_stacking
        self.milk_queue_high = milk_queue_high

    @property
    def workload_ratio(self) -> float:
        """Current workload as ratio of baseline (e.g. 1.5 = 50% above)."""
        if self.baseline_workload > 0:
            return self.workload_units_per_15min / self.baseline_workload
        return 0.0

    @property
    def drinks_in_progress(self) -> int:
        """Items currently being made (from KDS or estimate)."""
        if self.items_in_progress is not None:
            return self.items_in_progress
        # Rough estimate: orders_per_5min * 2 drinks avg * 3 (in pipeline)
        return self.orders_per_5min * 2

    @property
    def drinks_completed(self) -> int:
        """Completed drinks waiting for pickup/delivery."""
        if self.items_completed is not None:
            return self.items_completed
        return 0

    @property
    def orders_last_2min(self) -> int:
        """Estimate orders in last 2 minutes from 5-min rate."""
        # 2/5 of the 5-min rate, floored
        return int(self.orders_per_5min * 0.4)


def evaluate_transition_signals(signals: QueueSignals) -> list[dict]:
    """
    Evaluate all state transition conditions against current queue signals.

    Checks each transition from Spec Section 3.3 and returns a list
    of triggered transitions with their details.

    Returns:
        List of triggered transition dicts, each with:
        - transition: name string
        - conditions_met: list of condition descriptions
        - action: the action to take
        - owner_role: who should act
    """
    triggered = []

    # --- TRANSITION: 2P -> 3P Rush Mode (Section 3.3) ---
    t = TRANSITION_2P_TO_3P
    conditions_3p = []
    if signals.workload_ratio > t["workload_multiplier"]:
        conditions_3p.append(
            f"workload {signals.workload_ratio:.1f}x > {t['workload_multiplier']}x baseline"
        )
    if signals.orders_per_5min > t["orders_per_5min"]:
        conditions_3p.append(f"orders {signals.orders_per_5min}/5min > {t['orders_per_5min']}")
    if signals.drinks_in_progress > t["drinks_in_progress"]:
        conditions_3p.append(
            f"drinks in progress {signals.drinks_in_progress} > {t['drinks_in_progress']}"
        )

    # ALL conditions must be true for 2P->3P
    if len(conditions_3p) == 3 and signals.staff_on_floor == 2:
        triggered.append(
            {
                "transition": "SWITCH_TO_3P",
                "conditions_met": conditions_3p,
                "action": "Switch to 3P workflow",
                "owner_role": "MANAGER",
                "lead_time_minutes": t["lead_time_minutes"],
            }
        )

    # --- TRANSITION: Activate Wally Cycling (Section 3.3) ---
    t_wally = TRANSITION_WALLY_ACTIVATE
    wally_conditions = []
    if signals.milk_drinks_queued >= t_wally["milk_drinks_queued"]:
        wally_conditions.append(
            f"milk drinks queued {signals.milk_drinks_queued} >= {t_wally['milk_drinks_queued']}"
        )
    if signals.workload_ratio > t_wally["workload_multiplier"]:
        wally_conditions.append(
            f"workload {signals.workload_ratio:.1f}x > {t_wally['workload_multiplier']}x"
        )
    if signals.milk_queue_high:
        wally_conditions.append("manual milk_queue_high signal active")

    # ANY condition triggers Wally
    if wally_conditions:
        triggered.append(
            {
                "transition": "WALLY_START",
                "conditions_met": wally_conditions,
                "action": "Start Wally cycling",
                "owner_role": "P2",
            }
        )

    # --- TRANSITION: P1 Orders -> Shots (Section 3.3) ---
    t_p1 = TRANSITION_P1_TO_SHOTS
    if (
        signals.orders_last_2min <= t_p1["orders_last_2min"]
        and signals.drinks_in_progress >= t_p1["drinks_in_progress_min"]
    ):
        triggered.append(
            {
                "transition": "P1_TO_SHOTS",
                "conditions_met": [
                    f"orders last 2min = {signals.orders_last_2min}",
                    f"drinks in progress = {signals.drinks_in_progress}",
                ],
                "action": "P1 move to shots",
                "owner_role": "P1",
            }
        )

    # --- TRANSITION: Delivery Override (Section 3.3) ---
    t_del = TRANSITION_DELIVERY_OVERRIDE
    if (
        signals.drinks_completed >= t_del["drinks_completed"]
        and signals.orders_waiting <= t_del["orders_waiting_max"]
    ):
        triggered.append(
            {
                "transition": "DELIVERY_OVERRIDE",
                "conditions_met": [
                    f"drinks completed {signals.drinks_completed} >= {t_del['drinks_completed']}",
                    f"orders waiting {signals.orders_waiting} <= {t_del['orders_waiting_max']}",
                ],
                "action": "Delivery priority NOW",
                "owner_role": "P3",
            }
        )

    # --- TRANSITION: Rush End Reset (Section 3.3) ---
    t_end = TRANSITION_RUSH_END
    if signals.minutes_below_baseline >= t_end["below_baseline_minutes"]:
        triggered.append(
            {
                "transition": "RUSH_END",
                "conditions_met": [
                    f"below baseline for {signals.minutes_below_baseline} min "
                    f">= {t_end['below_baseline_minutes']} min"
                ],
                "action": "Return to 2P baseline",
                "owner_role": "MANAGER",
            }
        )

    return triggered
