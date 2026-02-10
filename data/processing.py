"""
Clubhouse Autopilot v1.2 - Data Processing
Workload calculation engine (Spec Sections 5.1-5.5)

Converts raw Square order items into workload units using:
- Base drink scores (Section 5.1)
- Modifier adjustments (Section 5.2)
- Multi-drink position penalties (Section 5.3)
- 15-minute timeline aggregation
- 4-layer composite prediction (Section 5.5)
"""

import logging
import math
from collections import defaultdict
from datetime import datetime
from typing import Optional

from config.constants import (
    BASE_DRINK_SCORES,
    DOW_PATTERN_DEFAULT,
    MILK_BUFFER_MULTIPLIER,
    MILK_DRINK_RATIO,
    MILK_PER_DRINK_ML,
    MILK_SPLIT_DEFAULT,
    MODIFIER_ADJUSTMENTS,
    POSITION_MULTIPLIERS,
)
from config.settings import settings

logger = logging.getLogger("autopilot.processing")


# ============================================================
# Name resolution: Square item names -> scoring keys
# ============================================================

DRINK_NAME_MAP = {
    "espresso": "espresso",
    "short black": "espresso",
    "long black": "long_black",
    "americano": "long_black",
    "latte": "latte",
    "cafe latte": "latte",
    "caffe latte": "latte",
    "cappuccino": "cappuccino",
    "cap": "cappuccino",
    "flat white": "flat_white",
    "flatwhite": "flat_white",
    "mocha": "mocha",
    "mochaccino": "mocha",
    "hot chocolate": "mocha",
    "iced latte": "iced_latte",
    "iced coffee": "iced_latte",
    "matcha": "matcha_complex",
    "chai": "matcha_complex",
}

MODIFIER_NAME_MAP = {
    "oat": "alt_milk",
    "oat milk": "alt_milk",
    "almond": "alt_milk",
    "almond milk": "alt_milk",
    "soy": "alt_milk",
    "soy milk": "alt_milk",
    "coconut": "alt_milk",
    "coconut milk": "alt_milk",
    "lactose free": "alt_milk",
    "extra shot": "extra_shot",
    "double shot": "extra_shot",
    "add shot": "extra_shot",
    "decaf": "decaf",
    "half caf": "decaf",
    "vanilla": "syrup",
    "caramel": "syrup",
    "hazelnut": "syrup",
    "syrup": "syrup",
    "iced": "iced",
    "large": "large",
    "lrg": "large",
}

MILK_DRINK_KEYS = {"latte", "cappuccino", "flat_white", "mocha", "iced_latte"}


def resolve_drink_key(item_name: str) -> str:
    """
    Map a Square item name to a BASE_DRINK_SCORES key.

    Uses case-insensitive substring matching. Longest match wins.
    Falls back to 'latte' (2.5 units) for unknowns as a
    conservative middle estimate.
    """
    name_lower = item_name.lower().strip()

    if name_lower in DRINK_NAME_MAP:
        return DRINK_NAME_MAP[name_lower]

    best_match = None
    best_len = 0
    for pattern, key in DRINK_NAME_MAP.items():
        if pattern in name_lower and len(pattern) > best_len:
            best_match = key
            best_len = len(pattern)

    if best_match:
        return best_match

    logger.debug("Unknown drink '%s', defaulting to latte score", item_name)
    return "latte"


def resolve_modifier_key(modifier_name: str) -> Optional[str]:
    """
    Map a Square modifier name to a MODIFIER_ADJUSTMENTS key.
    Returns None if the modifier doesn't affect workload.
    """
    name_lower = modifier_name.lower().strip()

    if name_lower in MODIFIER_NAME_MAP:
        return MODIFIER_NAME_MAP[name_lower]

    for pattern, key in MODIFIER_NAME_MAP.items():
        if pattern in name_lower:
            return key

    return None


# ============================================================
# Core workload calculation (Spec Sections 5.1-5.4)
# ============================================================


def calculate_item_workload(
    item_name: str,
    modifiers: list[dict],
    position_in_order: int,
    quantity: int = 1,
) -> dict:
    """
    Calculate workload units for a single line item.

    Exact formula from Spec Section 5.4:
      1. Look up base drink score (Section 5.1)
      2. Add modifier adjustments (Section 5.2)
      3. Apply position multiplier (Section 5.3)
         1st: 1.0x, 2nd: 1.3x, 3rd+: 1.6x
      4. Multiply by quantity
    """
    drink_key = resolve_drink_key(item_name)
    base = BASE_DRINK_SCORES.get(drink_key, BASE_DRINK_SCORES["latte"])

    base_units = base["units"]
    base_time = base["avg_time_sec"]

    # Section 5.2: modifier adjustments
    modifier_units = 0.0
    modifier_time = 0
    applied_mods = []

    for mod in modifiers:
        mod_key = resolve_modifier_key(mod.get("name", ""))
        if mod_key and mod_key in MODIFIER_ADJUSTMENTS:
            adj = MODIFIER_ADJUSTMENTS[mod_key]
            modifier_units += adj["units_add"]
            modifier_time += adj["time_add_sec"]
            applied_mods.append(mod_key)

    subtotal_units = base_units + modifier_units
    subtotal_time = base_time + modifier_time

    # Section 5.3: position multiplier
    pos_key = min(max(position_in_order, 1), 3)
    multiplier = POSITION_MULTIPLIERS.get(pos_key, 1.6)

    total_units = subtotal_units * multiplier * quantity
    total_time = int(subtotal_time * multiplier) * quantity

    return {
        "workload_units": round(total_units, 2),
        "prep_time_seconds": total_time,
        "drink_key": drink_key,
        "is_milk_drink": drink_key in MILK_DRINK_KEYS,
        "applied_modifiers": applied_mods,
        "base_units": base_units,
        "modifier_units": modifier_units,
        "position_multiplier": multiplier,
    }


def calculate_order_workload(parsed_order: dict) -> dict:
    """
    Calculate total workload for an entire order.

    Processes each line item with position-aware penalties per
    Spec Section 5.4 example:
        Order: 2x Oat Latte + 1x Decaf Cap
        Drink 1 (Oat Latte): 2.5 + 0.5 = 3.0 x 1.0 = 3.0
        Drink 2 (Oat Latte): 3.0 x 1.3 = 3.9
        Drink 3 (Decaf Cap): 2.5 + 0.3 = 2.8 x 1.6 = 4.48
        Total: 11.38 units

    Each unit of quantity gets its own position in the order,
    so 2x Latte = position 1 + position 2.
    """
    items_with_workload = []
    total_units = 0.0
    total_time = 0
    milk_drink_count = 0
    global_position = 0

    for li in parsed_order.get("line_items", []):
        qty = li.get("quantity", 1)

        # Each unit of quantity gets its own position
        for q in range(qty):
            global_position += 1

            workload = calculate_item_workload(
                item_name=li["item_name"],
                modifiers=li.get("modifiers", []),
                position_in_order=global_position,
                quantity=1,
            )

            total_units += workload["workload_units"]
            total_time += workload["prep_time_seconds"]

            if workload["is_milk_drink"]:
                milk_drink_count += 1

            items_with_workload.append({
                **li,
                "effective_position": global_position,
                "workload": workload,
            })

    return {
        **parsed_order,
        "line_items": items_with_workload,
        "total_workload_units": round(total_units, 2),
        "total_prep_time_seconds": total_time,
        "item_count": global_position,
        "milk_drink_count": milk_drink_count,
    }


# ============================================================
# Timeline aggregation (15-minute intervals)
# ============================================================


def aggregate_timeline(
    processed_orders: list[dict],
    interval_minutes: int = None,
) -> list[dict]:
    """
    Aggregate processed orders into 15-minute workload timeline buckets.

    Each bucket contains total workload units, order count, item count,
    and average prep time for that interval. This feeds the
    workload_timeline table in the schema.
    """
    if interval_minutes is None:
        interval_minutes = settings.WORKLOAD_INTERVAL_MINUTES

    buckets: dict[datetime, dict] = defaultdict(
        lambda: {
            "workload_units": 0.0,
            "orders_count": 0,
            "items_count": 0,
            "total_prep_seconds": 0,
        }
    )

    for order in processed_orders:
        closed_at_str = order.get("closed_at")
        if not closed_at_str:
            continue

        if isinstance(closed_at_str, str):
            closed_at_str = closed_at_str.replace("Z", "+00:00")
            closed_at = datetime.fromisoformat(closed_at_str)
        else:
            closed_at = closed_at_str

        # Floor to interval boundary
        minute_floor = (closed_at.minute // interval_minutes) * interval_minutes
        bucket_start = closed_at.replace(
            minute=minute_floor, second=0, microsecond=0
        )

        bucket = buckets[bucket_start]
        bucket["workload_units"] += order.get("total_workload_units", 0)
        bucket["orders_count"] += 1
        bucket["items_count"] += order.get("item_count", 0)
        bucket["total_prep_seconds"] += order.get("total_prep_time_seconds", 0)

    timeline = []
    for interval_start, data in sorted(buckets.items()):
        avg_prep = 0.0
        if data["items_count"] > 0:
            avg_prep = data["total_prep_seconds"] / data["items_count"]

        timeline.append({
            "interval_start": interval_start,
            "workload_units": round(data["workload_units"], 2),
            "orders_count": data["orders_count"],
            "items_count": data["items_count"],
            "avg_prep_seconds": round(avg_prep, 1),
        })

    logger.info(
        "Aggregated %d orders into %d timeline intervals",
        len(processed_orders), len(timeline),
    )
    return timeline


# ============================================================
# Wally volume calculation (Spec Section 4.4)
# ============================================================


def calculate_wally_volume(predicted_drinks: int) -> dict:
    """
    Calculate Wally milk volume requirements.

    Formula from Spec Section 4.4:
        predicted_milk_drinks = total_drinks * 0.70
        volume_L = (milk_drinks * 180ml * 1.2 buffer) / 1000

    Split by milk type:
        Full cream: 60%, Oat: 30%, Soy: 10%
    """
    milk_drinks = int(predicted_drinks * MILK_DRINK_RATIO)
    volume_ml = milk_drinks * MILK_PER_DRINK_ML * MILK_BUFFER_MULTIPLIER
    total_litres = math.ceil(volume_ml / 1000)

    split = {}
    for milk_type, ratio in MILK_SPLIT_DEFAULT.items():
        split[milk_type] = round(total_litres * ratio)

    # Fix rounding so split sums to total
    split_sum = sum(split.values())
    if split_sum != total_litres:
        split["full_cream"] += total_litres - split_sum

    return {
        "predicted_milk_drinks": milk_drinks,
        "total_litres": total_litres,
        "split": split,
    }


# ============================================================
# 4-Layer Composite Prediction (Spec Section 5.5)
# ============================================================


def predict_workload(
    recent_values: list[float],
    yoy_values: list[float],
    dow_name: str,
    event_multiplier: float = 1.0,
) -> dict:
    """
    Multi-layered workload prediction from Spec Section 5.5.

    Composite formula:
        base = (recent_avg * 0.60) + (yoy_avg * 0.25)
             + (recent_avg * dow_factor * 0.15)
        final = base * event_multiplier

    Rush threshold = base * 1.3

    Confidence scoring per Section 5.6:
        High (>=85%): Low variance, patterns match, no events
        Medium (70-84%): Some variance or uncertain event
        Low (<70%): Major deviation or unprecedented event
    """
    # Layer 1: Recent pattern (60% weight)
    if recent_values:
        recent_avg = sum(recent_values) / len(recent_values)
    else:
        recent_avg = 0.0
        logger.warning("No recent pattern data, using 0")

    # Layer 2: Year-over-year (25% weight)
    if yoy_values:
        yoy_avg = sum(yoy_values) / len(yoy_values)
    else:
        yoy_avg = recent_avg
        logger.info("No YoY data, falling back to recent average")

    # Layer 3: Day-of-week factor (15% weight)
    dow_factor = DOW_PATTERN_DEFAULT.get(dow_name, 1.0)

    # Composite: Spec Section 5.5 exact formula
    base_prediction = (
        recent_avg * settings.WEIGHT_RECENT
        + yoy_avg * settings.WEIGHT_YOY
        + (recent_avg * dow_factor) * settings.WEIGHT_DOW
    )

    # Layer 4: Apply event multiplier last
    final_prediction = base_prediction * event_multiplier

    # Rush threshold
    rush_threshold = base_prediction * settings.RUSH_THRESHOLD_MULTIPLIER

    # Confidence scoring
    confidence = _calculate_confidence(
        recent_values, yoy_values, recent_avg, yoy_avg, event_multiplier
    )

    return {
        "recent_avg": round(recent_avg, 1),
        "yoy_avg": round(yoy_avg, 1),
        "dow_factor": dow_factor,
        "dow_name": dow_name,
        "event_multiplier": event_multiplier,
        "base_prediction": round(base_prediction, 1),
        "final_prediction": round(final_prediction, 1),
        "rush_threshold": round(rush_threshold, 1),
        "is_rush": final_prediction > rush_threshold,
        "confidence": round(confidence, 2),
        "confidence_label": _confidence_label(confidence),
        "sample_sizes": {
            "recent": len(recent_values),
            "yoy": len(yoy_values),
        },
    }


def _calculate_confidence(
    recent_values: list[float],
    yoy_values: list[float],
    recent_avg: float,
    yoy_avg: float,
    event_multiplier: float,
) -> float:
    """
    Confidence score (0.0-1.0) per Spec Section 5.6.

    Deductions for:
    - Low sample size
    - High variance in recent data
    - Disagreement between recent and YoY
    - Non-standard event multiplier
    """
    score = 1.0

    # Sample size penalties
    if len(recent_values) < 4:
        score -= 0.15
    if len(recent_values) < 2:
        score -= 0.15
    if not yoy_values:
        score -= 0.10

    # Recent data variance (coefficient of variation)
    if len(recent_values) >= 2 and recent_avg > 0:
        variance = sum((x - recent_avg) ** 2 for x in recent_values) / len(recent_values)
        cv = (variance ** 0.5) / recent_avg
        if cv > 0.3:
            score -= 0.15
        elif cv > 0.15:
            score -= 0.05

    # Recent vs YoY disagreement
    if yoy_values and recent_avg > 0:
        deviation = abs(yoy_avg - recent_avg) / recent_avg
        if deviation > 0.25:
            score -= 0.15
        elif deviation > 0.10:
            score -= 0.05

    # Event uncertainty
    if event_multiplier != 1.0:
        score -= 0.05

    return max(0.0, min(1.0, score))


def _confidence_label(confidence: float) -> str:
    """Map confidence score to label per Spec Section 5.6."""
    if confidence >= 0.85:
        return "high"
    elif confidence >= 0.70:
        return "medium"
    return "low"


# ============================================================
# Batch processing pipeline
# ============================================================


def process_orders_batch(parsed_orders: list[dict]) -> dict:
    """
    Full processing pipeline for a batch of parsed orders.

    1. Calculate workload for each order (Sections 5.1-5.4)
    2. Aggregate into 15-minute timeline
    3. Compute summary statistics
    """
    processed = []
    total_units = 0.0
    total_items = 0
    total_milk = 0

    for order in parsed_orders:
        result = calculate_order_workload(order)
        processed.append(result)
        total_units += result["total_workload_units"]
        total_items += result["item_count"]
        total_milk += result["milk_drink_count"]

    timeline = aggregate_timeline(processed)

    summary = {
        "orders_count": len(processed),
        "items_count": total_items,
        "total_workload_units": round(total_units, 2),
        "milk_drink_count": total_milk,
        "milk_drink_pct": round(total_milk / max(total_items, 1) * 100, 1),
        "avg_units_per_order": round(total_units / max(len(processed), 1), 2),
        "timeline_intervals": len(timeline),
    }

    logger.info(
        "Processed %d orders: %d items, %.1f workload units",
        summary["orders_count"], summary["items_count"], summary["total_workload_units"],
    )

    return {
        "orders": processed,
        "timeline": timeline,
        "summary": summary,
    }
