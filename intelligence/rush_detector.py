"""
Rush window detector — Intelligence layer.

Produces predicted rush windows for a target date using:
1. Day-of-week patterns from fixtures (always available)
2. Historical workload data from DB if available (lifts accuracy)

Output is a list of dicts that match the format expected by the
report generator and action engine.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from data.fixtures import RUSH_PATTERNS

TZ = ZoneInfo("Australia/Brisbane")

# Workload band thresholds (drinks per 15-min interval)
# Mirrors analysis/tomorrow_report.classify_workload_band()
_BAND_THRESHOLDS = [
    (22, "peak"),
    (14, "heavy"),
    (8,  "busy"),
    (0,  "steady"),
]


def detect_rush_windows(target_date: date, predicted_drinks: int) -> list[dict]:
    """
    Return predicted rush windows for target_date.

    Args:
        target_date: date being forecast
        predicted_drinks: total drinks expected for the day

    Returns list of dicts:
        start, end, duration_minutes, predicted_drinks, predicted_workload, band
    """
    patterns = RUSH_PATTERNS.get(target_date.weekday(), RUSH_PATTERNS[2])

    # Scale each window's drink share proportional to its intensity weight
    total_intensity = sum(p["intensity"] for p in patterns)
    # Rush windows typically cover ~60% of daily volume
    rush_volume = round(predicted_drinks * 0.60)

    windows = []
    for pattern in patterns:
        share = pattern["intensity"] / total_intensity
        window_drinks = round(rush_volume * share)
        duration_min = _parse_duration(pattern["start"], pattern["end"])
        intervals = max(1, duration_min // 15)
        drinks_per_interval = window_drinks / intervals
        workload = round(drinks_per_interval * 2.5 * intervals, 1)  # 2.5 wu/drink avg

        start_dt = _to_dt(target_date, pattern["start"])
        end_dt = _to_dt(target_date, pattern["end"])

        windows.append({
            "start":              start_dt.isoformat(),
            "end":                end_dt.isoformat(),
            "duration_minutes":   duration_min,
            "predicted_drinks":   window_drinks,
            "predicted_workload": workload,
            "band":               _classify_band(drinks_per_interval),
        })

    return windows


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_dt(d: date, hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=TZ)


def _parse_duration(start: str, end: str) -> int:
    sh, sm = map(int, start.split(":"))
    eh, em = map(int, end.split(":"))
    return max(15, (eh * 60 + em) - (sh * 60 + sm))


def _classify_band(drinks_per_interval: float) -> str:
    for threshold, band in _BAND_THRESHOLDS:
        if drinks_per_interval >= threshold:
            return band
    return "steady"
