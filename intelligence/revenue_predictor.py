"""
Revenue predictor — Intelligence layer.

Produces a revenue + drinks forecast from historical daily sales data.
Uses a weighted blend of recent trend (60%), same-DOW history (25%),
and year-on-year comparison (15%) — mirroring the spec's 4-layer model
without requiring a live DB connection.

Falls back gracefully when data is sparse.
"""

from __future__ import annotations

from datetime import date
from statistics import mean

# Weights match config/settings.py WEIGHT_RECENT / WEIGHT_DOW / WEIGHT_YOY
_W_RECENT = 0.60
_W_DOW = 0.25
_W_YOY = 0.15

# Fallback calibrated to a single-venue specialty cafe (from spec)
_FALLBACK_REVENUE_CENTS = 248_000      # ~$2,480/day
_FALLBACK_DRINKS = 270
_FALLBACK_REV_PER_DRINK = 917          # $9.17


def predict_revenue(historical_days: list[dict], target_date: date) -> dict:
    """
    Predict revenue and drink volume for target_date.

    Args:
        historical_days: list of dicts with keys:
            date (date), revenue_cents (int), drink_count (int)
        target_date: the date to forecast

    Returns dict with keys:
        predicted_cents, predicted_drinks, confidence, label,
        avg_revenue_per_drink_cents, based_on_days
    """
    valid = [
        d for d in (historical_days or [])
        if int(d.get("revenue_cents") or 0) > 0 and int(d.get("drink_count") or 0) > 0
    ]

    if len(valid) < 3:
        return _fallback(target_date, note="insufficient history")

    # ── Component 1: recent 14-day average ───────────────────────────────────
    recent = sorted(valid, key=lambda x: x["date"])[-14:]
    recent_drinks = mean(int(r["drink_count"]) for r in recent)
    recent_rev_per_drink = mean(
        int(r["revenue_cents"]) / int(r["drink_count"]) for r in recent
    )

    # ── Component 2: same day-of-week ────────────────────────────────────────
    target_dow = target_date.weekday()
    dow_days = [d for d in valid if d["date"].weekday() == target_dow]
    if dow_days:
        dow_drinks = mean(int(d["drink_count"]) for d in dow_days)
        dow_rev_per_drink = mean(
            int(d["revenue_cents"]) / int(d["drink_count"]) for d in dow_days
        )
    else:
        dow_drinks = recent_drinks
        dow_rev_per_drink = recent_rev_per_drink

    # ── Component 3: year-over-year (same DOW, ~52 weeks back) ───────────────
    # Use DOW as proxy since we rarely have a full year of data locally
    yoy_drinks = dow_drinks  # degrade gracefully
    yoy_rev_per_drink = dow_rev_per_drink

    # ── Weighted blend ────────────────────────────────────────────────────────
    predicted_drinks = round(
        recent_drinks * _W_RECENT + dow_drinks * _W_DOW + yoy_drinks * _W_YOY
    )
    avg_rev_per_drink = (
        recent_rev_per_drink * _W_RECENT
        + dow_rev_per_drink * _W_DOW
        + yoy_rev_per_drink * _W_YOY
    )
    predicted_cents = round(predicted_drinks * avg_rev_per_drink)

    # Confidence rises with data volume; caps at 0.92
    confidence = min(0.92, 0.45 + len(valid) * 0.016)
    label = "high" if confidence >= 0.80 else "medium" if confidence >= 0.60 else "low"

    return {
        "predicted_cents":            predicted_cents,
        "predicted_drinks":           predicted_drinks,
        "confidence":                 round(confidence, 2),
        "label":                      label,
        "avg_revenue_per_drink_cents": round(avg_rev_per_drink),
        "based_on_days":              len(valid),
    }


def _fallback(target_date: date, note: str = "") -> dict:
    """Return calibrated baseline when history is absent."""
    return {
        "predicted_cents":            _FALLBACK_REVENUE_CENTS,
        "predicted_drinks":           _FALLBACK_DRINKS,
        "confidence":                 0.50,
        "label":                      "low",
        "avg_revenue_per_drink_cents": _FALLBACK_REV_PER_DRINK,
        "based_on_days":              0,
        "note":                       note or "fallback baseline used",
    }
