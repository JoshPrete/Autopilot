"""
Synthetic fixture data for offline / demo mode.

Used by pipeline.py when DATABASE_URL is unavailable or --offline is set.
All values are calibrated to match a real single-venue specialty cafe.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Australia/Brisbane")

# Realistic baseline metrics (calibrated from spec)
_AVG_REVENUE_CENTS = 280_000   # ~$2,800/day
_AVG_DRINK_COUNT = 290
_AVG_LABOR_CENTS = 84_000      # ~30% labor
_AVG_ORDER_COUNT = 115

# Day-of-week revenue multipliers  (Mon=0 … Sun=6)
_DOW_FACTOR = {0: 0.90, 1: 0.85, 2: 0.88, 3: 0.95, 4: 1.15, 5: 1.25, 6: 0.82}

# Typical rush patterns per weekday  (Mon=0 … Sun=6)
# Each entry: {"start": "HH:MM", "end": "HH:MM", "intensity": 0-1}
RUSH_PATTERNS: dict[int, list[dict]] = {
    0: [  # Monday
        {"start": "07:30", "end": "09:00", "intensity": 0.80},
        {"start": "11:30", "end": "12:15", "intensity": 0.55},
    ],
    1: [  # Tuesday
        {"start": "07:30", "end": "08:45", "intensity": 0.75},
        {"start": "12:00", "end": "12:45", "intensity": 0.50},
    ],
    2: [  # Wednesday
        {"start": "07:30", "end": "09:00", "intensity": 0.80},
        {"start": "10:30", "end": "11:15", "intensity": 0.55},
    ],
    3: [  # Thursday
        {"start": "07:15", "end": "09:00", "intensity": 0.85},
        {"start": "10:30", "end": "11:30", "intensity": 0.60},
    ],
    4: [  # Friday
        {"start": "07:00", "end": "09:15", "intensity": 0.95},
        {"start": "10:00", "end": "11:00", "intensity": 0.70},
        {"start": "12:30", "end": "13:15", "intensity": 0.55},
    ],
    5: [  # Saturday
        {"start": "08:00", "end": "10:30", "intensity": 1.00},
        {"start": "11:30", "end": "12:30", "intensity": 0.65},
    ],
    6: [  # Sunday
        {"start": "08:30", "end": "10:30", "intensity": 0.85},
        {"start": "11:00", "end": "12:00", "intensity": 0.50},
    ],
}

DEMO_STAFF = [
    {"employee_name": "Sarah Chen",   "start_h": 6,  "end_h": 14, "cost_dollars": 190.00},
    {"employee_name": "Tom Wilson",   "start_h": 7,  "end_h": 15, "cost_dollars": 175.00},
    {"employee_name": "Jessica Park", "start_h": 10, "end_h": 18, "cost_dollars": 160.00},
]

DEMO_SITE = {
    "site_id":   "00000000-0000-0000-0000-000000000001",
    "site_name": "Clubhouse Demo",
}


# ── Public API ────────────────────────────────────────────────────────────────

def load_demo_data(run_date: date | None = None) -> dict:
    """
    Return a complete data bundle suitable for the pipeline intelligence layer.

    run_date: the "today" business date; forecast is run_date + 1 day.
    """
    run_date = run_date or date.today()
    return {
        **DEMO_SITE,
        "historical":  _historical_sales(run_date, days=28),
        "roster":      _tomorrow_roster(run_date),
        "source":      "fixture",
    }


def historical_sales(days: int = 28, anchor: date | None = None) -> list[dict]:
    """Public alias — synthetic daily sales for the last `days` days."""
    anchor = anchor or date.today()
    return _historical_sales(anchor, days=days)


def tomorrow_roster(run_date: date | None = None) -> list[dict]:
    """Public alias — synthetic roster for tomorrow."""
    return _tomorrow_roster(run_date or date.today())


# ── Private helpers ───────────────────────────────────────────────────────────

def _dow_revenue(d: date) -> int:
    factor = _DOW_FACTOR.get(d.weekday(), 1.0)
    return round(_AVG_REVENUE_CENTS * factor)


def _dow_drinks(d: date) -> int:
    factor = _DOW_FACTOR.get(d.weekday(), 1.0)
    return round(_AVG_DRINK_COUNT * factor)


def _historical_sales(anchor: date, days: int) -> list[dict]:
    rows = []
    for i in range(days, 0, -1):
        d = anchor - timedelta(days=i)
        rows.append({
            "date":           d,
            "revenue_cents":  _dow_revenue(d),
            "drink_count":    _dow_drinks(d),
            "labor_cents":    round(_AVG_LABOR_CENTS * _DOW_FACTOR.get(d.weekday(), 1.0)),
            "order_count":    round(_AVG_ORDER_COUNT * _DOW_FACTOR.get(d.weekday(), 1.0)),
        })
    return rows


def _tomorrow_roster(run_date: date) -> list[dict]:
    tomorrow = run_date + timedelta(days=1)
    shifts = []
    for staff in DEMO_STAFF:
        start = datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                         staff["start_h"], 0, 0, tzinfo=TZ)
        end = datetime(tomorrow.year, tomorrow.month, tomorrow.day,
                       staff["end_h"], 0, 0, tzinfo=TZ)
        shifts.append({
            "employee_name": staff["employee_name"],
            "shift_date":    tomorrow,
            "start_time":    start,
            "end_time":      end,
            "total_hours":   staff["end_h"] - staff["start_h"],
            "cost_dollars":  staff["cost_dollars"],
            "is_published":  True,
        })
    return shifts
