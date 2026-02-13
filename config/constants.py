# ============================================================
# Clubhouse Autopilot v1.2 - Constants
# Immutable policy values from the spec
# ============================================================

# ------------------------------------------------------------
# Priority Stack (Universal Law - Section 2.1)
# ------------------------------------------------------------
PRIORITY_STACK = {
    1: "Serve customers (greet, take orders)",
    2: "Deliver drinks/food",
    3: "Shots (pull espresso, prep bases)",
    4: "Prep cups (stage for rush)",
}

# ------------------------------------------------------------
# State Machine States (Section 3.1)
# ------------------------------------------------------------
STATES = {
    "S1P_SURVIVAL": {"staff": 1, "purpose": "Keep flow alive"},
    "S2P_STANDARD": {"staff": 2, "purpose": "Baseline mode"},
    "S2P_BUSY": {"staff": 2, "purpose": "Building pressure"},
    "S3P_RUSH": {"staff": 3, "purpose": "Rush execution"},
    "S4P_STANDARD": {"staff": 4, "purpose": "High consistency"},
}

# ------------------------------------------------------------
# Base Drink Scores (Section 5.1)
# ------------------------------------------------------------
# Calibrated from 6,000+ KDS tickets (Jan-Feb 2026)
# avg_time_sec = P25 of single-item peak-hour tickets (pure prep proxy)
BASE_DRINK_SCORES = {
    "espresso": {"units": 1.0, "avg_time_sec": 90},     # 4oz P25=98s
    "long_black": {"units": 1.2, "avg_time_sec": 95},   # similar to espresso + water
    "latte": {"units": 2.5, "avg_time_sec": 115},       # 12oz P25=116s
    "cappuccino": {"units": 2.5, "avg_time_sec": 115},   # same as latte
    "flat_white": {"units": 2.8, "avg_time_sec": 100},  # 8oz P25=104s
    "mocha": {"units": 3.5, "avg_time_sec": 130},       # latte + chocolate prep
    "iced_latte": {"units": 3.2, "avg_time_sec": 110},  # 12oz iced P25=109s, 16oz P25=118s
    "matcha_complex": {"units": 4.0, "avg_time_sec": 140},  # complex prep
    "babycino": {"units": 0.5, "avg_time_sec": 30},     # froth + serve
}

# ------------------------------------------------------------
# Base Food Scores (calibrated from KDS data)
# ------------------------------------------------------------
BASE_FOOD_SCORES = {
    "toastie":       {"units": 1.8, "avg_time_sec": 190},  # KDS P25=190s, press + serve
    "wrap":          {"units": 1.5, "avg_time_sec": 60},   # KDS P25=57s
    "croissant":     {"units": 0.5, "avg_time_sec": 35},   # grab from case, KDS varies
    "muffin":        {"units": 0.5, "avg_time_sec": 30},   # grab + plate
    "pastry":        {"units": 0.5, "avg_time_sec": 35},   # KDS P25=36s
    "cookie":        {"units": 0.3, "avg_time_sec": 15},
    "tart":          {"units": 0.5, "avg_time_sec": 35},
}

# ------------------------------------------------------------
# Base Retail Scores (calibrated from KDS data)
# ------------------------------------------------------------
BASE_RETAIL_SCORES = {
    "water":         {"units": 0.2, "avg_time_sec": 30},   # KDS P25=30s
    "juice":         {"units": 0.2, "avg_time_sec": 15},   # KDS P25=14s
    "soda":          {"units": 0.2, "avg_time_sec": 15},   # similar to juice
    "kombucha":      {"units": 0.2, "avg_time_sec": 16},   # KDS P25=16s
    "beans":         {"units": 0.3, "avg_time_sec": 65},   # KDS P25=66s, weigh + bag
    "gift_card":     {"units": 0.1, "avg_time_sec": 30},
    "merchandise":   {"units": 0.1, "avg_time_sec": 15},
}

# ------------------------------------------------------------
# KDS Rush Indicator (Section 3.2)
# Median ticket completion > 5 min during peak (5am-11am)
# signals queue backup / understaffing
# ------------------------------------------------------------
KDS_RUSH_THRESHOLD_SEC = 300  # 5 minutes

# ------------------------------------------------------------
# Modifier Adjustments (Section 5.2)
# ------------------------------------------------------------
MODIFIER_ADJUSTMENTS = {
    "alt_milk": {"units_add": 0.5, "time_add_sec": 10},
    "extra_shot": {"units_add": 0.5, "time_add_sec": 8},
    "decaf": {"units_add": 0.3, "time_add_sec": 5},
    "syrup": {"units_add": 0.4, "time_add_sec": 8},
    "iced": {"units_add": 0.3, "time_add_sec": 12},
    "large": {"units_add": 0.2, "time_add_sec": 5},
}

# ------------------------------------------------------------
# Multi-Drink Position Penalties (Section 5.3)
# ------------------------------------------------------------
POSITION_MULTIPLIERS = {
    1: 1.0,
    2: 1.3,
    3: 1.6,  # 3rd and all subsequent drinks
}

# ------------------------------------------------------------
# Wally Module (Section 4)
# ------------------------------------------------------------
WALLY_MODES = {
    "OFF": "Not in use",
    "ON_DEMAND": "Single batch, then off",
    "CYCLING": "Continuous jug turnover",
}

MILK_PER_DRINK_ML = 180
MILK_BUFFER_MULTIPLIER = 1.2

# Default milk split (Section 4.4)
MILK_SPLIT_DEFAULT = {
    "full_cream": 0.60,
    "oat": 0.30,
    "soy": 0.10,
}

# Milk-based drink ratio (from spec example)
MILK_DRINK_RATIO = 0.70

# Drink keys that require milk steaming
MILK_DRINK_KEYS = {"latte", "cappuccino", "flat_white", "mocha", "iced_latte"}

# ------------------------------------------------------------
# Transition Thresholds (Section 3.3)
# ------------------------------------------------------------
TRANSITION_2P_TO_3P = {
    "workload_multiplier": 1.5,   # > baseline x 1.5
    "orders_per_5min": 6,         # > 6 orders / 5 min
    "drinks_in_progress": 8,      # > 8 items
    "lead_time_minutes": 7,       # T-7 before rush
    "alert_lead_minutes": 10,     # SMS at T-10
}

TRANSITION_WALLY_ACTIVATE = {
    "milk_drinks_queued": 3,      # >= 3 lattes/caps
    "rush_predicted_minutes": 15, # within 15 min
    "workload_multiplier": 1.3,   # > baseline x 1.3
}

TRANSITION_P1_TO_SHOTS = {
    "orders_last_2min": 0,        # = 0
    "drinks_in_progress_min": 1,  # > 0
}

TRANSITION_DELIVERY_OVERRIDE = {
    "drinks_completed": 3,        # >= 3 drinks ready
    "orders_waiting_max": 1,      # <= 1
}

TRANSITION_RUSH_END = {
    "below_baseline_minutes": 10, # 10 min sustained
}

# ------------------------------------------------------------
# Day-of-Week Pattern Defaults (Section 5.5)
# ------------------------------------------------------------
DOW_PATTERN_DEFAULT = {
    "Monday": 0.85,
    "Tuesday": 0.92,
    "Wednesday": 0.98,
    "Thursday": 1.05,
    "Friday": 1.12,
    "Saturday": 1.15,
    "Sunday": 0.93,
}

# ------------------------------------------------------------
# Role Definitions (Section 2.2)
# ------------------------------------------------------------
ROLES = {
    "P1": {
        "primary": "Front / Orders / Customer Service",
        "secondary": "Shots when counter clear",
    },
    "P2": {
        "primary": "Shots + Milk Operation + Wally",
        "secondary": "Finish drinks when shots clear",
    },
    "P3": {
        "primary": "Delivery / Runner / Prep Support",
        "secondary": "Counter overflow if needed",
    },
    "MANAGER": {
        "primary": "Oversight + escalation target",
        "secondary": "Fill any role as needed",
    },
}
