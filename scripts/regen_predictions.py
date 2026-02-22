#!/usr/bin/env python3
"""
Regenerate predictions for the past N days and re-score accuracy.

After re-ingesting/re-scoring order data, this script:
1. Regenerates predictions for each of the past N days
2. Computes actual drink counts from re-scored order_items (drinks only)
3. Updates prediction accuracy against drinks-only actuals

Usage:
    python scripts/regen_predictions.py              # last 7 days
    python scripts/regen_predictions.py --days 14     # last 14 days
    python scripts/regen_predictions.py --dry-run     # preview only
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from config.database import engine
from config.settings import settings
from data.storage import get_site_by_location_id
from models.prediction import generate_prediction

logger = logging.getLogger("autopilot.regen")


def _text(sql: str):
    from sqlalchemy import text

    return text(sql)


def get_actual_counts(site_id: str, target_date: date) -> dict:
    """
    Get actual drink/food/total counts for a given date from re-scored order_items.

    Uses the closed_at timestamp (stored as created_at in order_items)
    converted from UTC to AEST (UTC+10) for date matching.
    """
    with engine.connect() as conn:
        result = (
            conn.execute(
                _text(
                    """
                SELECT
                    COUNT(*) AS total_items,
                    SUM(workload_units) AS total_workload
                FROM order_items oi
                JOIN orders_raw o ON oi.order_id = o.order_id
                WHERE oi.site_id = :sid
                  AND (o.closed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Australia/Brisbane')::date = :dt
            """
                ),
                {"sid": site_id, "dt": target_date},
            )
            .mappings()
            .first()
        )

        # Get drink-only count using the new category data
        # Items with workload >= 1.0 that aren't food/retail are drinks
        # More precisely: items whose names map to drink score keys
        drink_result = (
            conn.execute(
                _text(
                    """
                SELECT COUNT(*) AS drink_count
                FROM order_items oi
                JOIN orders_raw o ON oi.order_id = o.order_id
                WHERE oi.site_id = :sid
                  AND (o.closed_at AT TIME ZONE 'UTC' AT TIME ZONE 'Australia/Brisbane')::date = :dt
                  AND oi.item_name NOT IN (
                      'Sweet Pastry', 'TOASTIE', 'A Sweet Muffin', 'A Savoury Muffin',
                      'Breakfast Wrap', 'Ham And Cheese Croissant', 'Plain Croissant',
                      'BUTTERBOY', 'A Cookie',
                      'Portugese Tart/Friand/Caramel Slice',
                      'Fiji Water', 'Fruit Juice', 'Kombucha', 'Famous Soda', 'Black Mass',
                      '500g Beans', '1kg Beans', '250g Beans',
                      'Candle', 'eGift Card', 'Mary Clothes'
                  )
            """
                ),
                {"sid": site_id, "dt": target_date},
            )
            .mappings()
            .first()
        )

    return {
        "total_items": result["total_items"] if result else 0,
        "total_workload": (
            float(result["total_workload"]) if result and result["total_workload"] else 0.0
        ),
        "drink_count": drink_result["drink_count"] if drink_result else 0,
    }


def clear_prediction(site_id: str, forecast_date: date):
    """Delete existing prediction for a date so we can regenerate."""
    with engine.connect() as conn:
        # Clear recommendations first (FK constraint)
        conn.execute(
            _text(
                """
                DELETE FROM recommendations
                WHERE prediction_id IN (
                    SELECT prediction_id FROM predictions
                    WHERE site_id = :sid AND forecast_date = :fd
                )
            """
            ),
            {"sid": site_id, "fd": forecast_date},
        )
        conn.execute(
            _text("DELETE FROM predictions WHERE site_id = :sid AND forecast_date = :fd"),
            {"sid": site_id, "fd": forecast_date},
        )
        conn.commit()


def update_accuracy(
    site_id: str, forecast_date: date, actual_drinks: int, actual_workload: float
) -> float | None:
    """Compute and store accuracy for a prediction."""
    with engine.connect() as conn:
        pred = (
            conn.execute(
                _text(
                    "SELECT forecast_data FROM predictions "
                    "WHERE site_id = :sid AND forecast_date = :fd"
                ),
                {"sid": site_id, "fd": forecast_date},
            )
            .mappings()
            .first()
        )

    if not pred:
        return None

    forecast_data = pred["forecast_data"]
    if isinstance(forecast_data, str):
        forecast_data = json.loads(forecast_data)

    predicted_drinks = forecast_data.get("total_predicted_drinks", 0)

    if actual_drinks <= 0:
        return None

    error = abs(predicted_drinks - actual_drinks) / actual_drinks
    accuracy = max(0.0, min(1.0, 1.0 - error))

    with engine.connect() as conn:
        conn.execute(
            _text(
                "UPDATE predictions SET actual_accuracy = :acc "
                "WHERE site_id = :sid AND forecast_date = :fd"
            ),
            {"acc": accuracy, "sid": site_id, "fd": forecast_date},
        )
        conn.commit()

    return accuracy


def main():
    p = argparse.ArgumentParser(description="Regenerate predictions and re-score accuracy")
    p.add_argument("--days", type=int, default=7, help="Number of past days (default: 7)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Quiet down noisy loggers
    logging.getLogger("autopilot.storage").setLevel(logging.WARNING)
    logging.getLogger("autopilot.processing").setLevel(logging.WARNING)
    logging.getLogger("autopilot.workload").setLevel(logging.WARNING)

    site = get_site_by_location_id(settings.SQUARE_LOCATION_ID)
    if not site:
        logger.error("No site found")
        return 1

    site_id = str(site["site_id"])
    logger.info("Regenerating predictions for %s", site["name"])

    today = date.today()
    results = []

    for days_ago in range(args.days, 0, -1):
        target_date = today - timedelta(days=days_ago)

        # Get actuals for this date
        actuals = get_actual_counts(site_id, target_date)

        if actuals["total_items"] == 0:
            logger.info("%s: No order data, skipping", target_date)
            continue

        if not args.dry_run:
            # Clear old prediction
            clear_prediction(site_id, target_date)

            # Regenerate prediction
            try:
                prediction = generate_prediction(
                    site_id=site_id,
                    target_date=target_date,
                    save=True,
                )
                predicted_drinks = prediction["total_predicted_drinks"]
                predicted_workload = prediction["total_predicted_workload"]
            except Exception as e:
                logger.error("%s: Prediction failed: %s", target_date, e)
                continue

            # Score accuracy against drinks-only actual count
            accuracy = update_accuracy(
                site_id,
                target_date,
                actuals["drink_count"],
                actuals["total_workload"],
            )
        else:
            predicted_drinks = "?"
            predicted_workload = "?"
            accuracy = None

        result = {
            "date": target_date,
            "day": target_date.strftime("%A"),
            "actual_total": actuals["total_items"],
            "actual_drinks": actuals["drink_count"],
            "actual_workload": round(actuals["total_workload"], 1),
            "predicted_drinks": predicted_drinks,
            "accuracy": accuracy,
        }
        results.append(result)

        acc_str = f"{accuracy*100:.1f}%" if accuracy else "n/a"
        logger.info(
            "%s (%s): predicted=%s actual_drinks=%d actual_total=%d accuracy=%s",
            target_date,
            result["day"],
            predicted_drinks,
            actuals["drink_count"],
            actuals["total_items"],
            acc_str,
        )

    # Summary
    print("\n" + "=" * 75)
    print(f"{'Date':<12} {'Day':<10} {'Pred':>6} {'Actual':>7} {'Total':>6} {'Accuracy':>9}")
    print("-" * 75)
    for r in results:
        acc = f"{r['accuracy']*100:.1f}%" if r["accuracy"] else "n/a"
        print(
            f"{r['date']}  {r['day']:<10} {str(r['predicted_drinks']):>6} "
            f"{r['actual_drinks']:>7} {r['actual_total']:>6} {acc:>9}"
        )

    accuracies = [r["accuracy"] for r in results if r["accuracy"] is not None]
    if accuracies:
        avg_acc = sum(accuracies) / len(accuracies)
        print("-" * 75)
        print(f"{'Average':>22} {' ':>20} {avg_acc*100:.1f}%")
    print("=" * 75)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
