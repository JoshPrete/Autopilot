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
    "butterboy":     {"units": 0.3, "avg_time_sec": 15},
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
# Staffing Variance Heuristics (15-min intervals)
# ------------------------------------------------------------
# Workload units handled per staff member in a 15-minute window.
STAFFING_WU_PER_PERSON_TARGET = 3.0
STAFFING_WU_PER_PERSON_HIGH = 3.8
STAFFING_WU_PER_PERSON_LOW = 1.8

# ------------------------------------------------------------
# Staffing Efficiency Gap Engine
# ------------------------------------------------------------
LABOR_COST_PER_PERSON_PER_INTERVAL_CENTS = 650  # ~$26/hr per 15-min (fallback)
EFFICIENCY_SCORE_TARGET = 0.85   # 85% = 15% buffer acceptable
EFFICIENCY_SCORE_WARNING = 0.70  # Below this = critical
JUNIOR_HOURLY_RATE_THRESHOLD = 2100  # cents — under this = under-18 junior (Aus award proxy)
SUPERANNUATION_RATE = 0.115  # 11.5% from 1 Jul 2025
GST_RATE = 0.10  # Australian GST — Xero P&L is ex-GST, Square is inc-GST

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
# ------------------------------------------------------------
# Default Item Costs (COGS) — Brisbane Specialty Cafe Estimates
# Cost in cents per unit. Seeds item_costs table on first run.
# Based on: beans ($45/kg, ~18g/shot), milk ($2.50/L), cups, lids,
#           wholesale food costs from Brisbane suppliers.
# ------------------------------------------------------------
# ------------------------------------------------------------
# Owner Salary — amortised daily into labor costs
# ------------------------------------------------------------
OWNER_DEPUTY_NAME = "Josh Prete"  # Must match Deputy roster employee_name
OWNER_ANNUAL_SALARY_CENTS = 10_000_000  # $100,000 AUD
OWNER_DAILY_SALARY_CENTS = round(OWNER_ANNUAL_SALARY_CENTS / 365)  # ~$273.97/day
OWNER_HOURLY_RATE_CENTS = round(OWNER_ANNUAL_SALARY_CENTS / (52 * 40))  # ~$48.08/hr based on 40hr weeks

DEFAULT_ITEM_COSTS = {
    # Drinks — cost = beans + milk + cup + lid
    "espresso":        {"cost_cents": 80,  "category": "drink",  "description": "18g beans + 4oz cup"},
    "long_black":      {"cost_cents": 90,  "category": "drink",  "description": "18g beans + 8oz cup + water"},
    "latte":           {"cost_cents": 140, "category": "drink",  "description": "18g beans + 220ml milk + 12oz cup"},
    "cappuccino":      {"cost_cents": 140, "category": "drink",  "description": "18g beans + 180ml milk + 12oz cup"},
    "flat_white":      {"cost_cents": 120, "category": "drink",  "description": "18g beans + 160ml milk + 8oz cup"},
    "mocha":           {"cost_cents": 170, "category": "drink",  "description": "18g beans + 200ml milk + chocolate + 12oz cup"},
    "iced_latte":      {"cost_cents": 160, "category": "drink",  "description": "18g beans + 200ml milk + ice + 16oz cup"},
    "matcha_complex":  {"cost_cents": 200, "category": "drink",  "description": "matcha powder + 220ml milk + 12oz cup"},
    "babycino":        {"cost_cents": 30,  "category": "drink",  "description": "froth + small cup + marshmallow"},
    # Food — wholesale cost estimates
    "toastie":         {"cost_cents": 250, "category": "food",   "description": "bread + ham/cheese + packaging"},
    "wrap":            {"cost_cents": 280, "category": "food",   "description": "tortilla + fillings + packaging"},
    "croissant":       {"cost_cents": 180, "category": "food",   "description": "wholesale bakery + filling"},
    "muffin":          {"cost_cents": 150, "category": "food",   "description": "wholesale bakery"},
    "pastry":          {"cost_cents": 160, "category": "food",   "description": "wholesale bakery assorted"},
    "cookie":          {"cost_cents": 100, "category": "food",   "description": "wholesale bakery"},
    "tart":            {"cost_cents": 170, "category": "food",   "description": "wholesale bakery"},
    # Retail — wholesale cost
    "water":           {"cost_cents": 100, "category": "retail", "description": "wholesale bottled water"},
    "juice":           {"cost_cents": 180, "category": "retail", "description": "wholesale fruit juice"},
    "soda":            {"cost_cents": 120, "category": "retail", "description": "wholesale soda"},
    "kombucha":        {"cost_cents": 220, "category": "retail", "description": "wholesale kombucha"},
    "beans":           {"cost_cents": 1500, "category": "retail", "description": "250g retail beans (avg)"},
    "gift_card":       {"cost_cents": 0,   "category": "retail", "description": "no COGS — stored value"},
    "merchandise":     {"cost_cents": 800, "category": "retail", "description": "avg wholesale merch cost"},
}

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
