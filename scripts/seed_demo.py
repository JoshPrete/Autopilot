#!/usr/bin/env python3
"""
Demo seed script — populates the DB with enough data to make `make tomorrow` succeed
without any real Square/Deputy/Xero integration.

Usage:
    .venv/bin/python scripts/seed_demo.py

What it creates:
  - 1 demo site (idempotent via ON CONFLICT DO NOTHING)
  - 30 days of daily_profitability history
  - 30 days of daily_sales_history
  - 5 completed orders_raw rows for TODAY (satisfies ingest diagnostics)
  - 3 deputy roster shifts for TOMORROW (satisfies labor cost check)

Run once after `make db-stamp && make migrate`. Safe to re-run (idempotent).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

if not os.getenv("DATABASE_URL"):
    print(
        "ERROR: DATABASE_URL is not set. Add it to .env before seeding.",
        file=sys.stderr,
    )
    sys.exit(1)

from sqlalchemy import text

from config.database import engine

# ── Constants ────────────────────────────────────────────────────────────────

DEMO_SITE_ID = "00000000-0000-0000-0000-000000000001"
DEMO_SITE_NAME = "Clubhouse Demo"
# Use env var so demo matches real Square location; fall back to a placeholder
DEMO_SQUARE_LOCATION_ID = os.getenv("SQUARE_LOCATION_ID", "DEMO_LOCATION_001")

TZ = ZoneInfo("Australia/Brisbane")
TODAY = date.today()
TOMORROW = TODAY + timedelta(days=1)

# Realistic cafe metrics
AVG_REVENUE_CENTS = 320_000   # ~$3,200/day
AVG_DRINK_COUNT = 280
AVG_LABOR_CENTS = 96_000      # ~30% labor
AVG_ORDER_COUNT = 110

STAFF = [
    {"name": "Sarah Chen", "start_h": 6, "end_h": 14, "cost": 190.00},
    {"name": "Tom Wilson", "start_h": 7, "end_h": 15, "cost": 175.00},
    {"name": "Jessica Park", "start_h": 10, "end_h": 18, "cost": 160.00},
]


# ── Helpers ──────────────────────────────────────────────────────────────────

def _jitter(base: int, pct: float = 0.10) -> int:
    """Return base ± pct variance, deterministically seeded by date."""
    import hashlib
    seed = int(hashlib.md5(str(TODAY).encode()).hexdigest()[:8], 16)
    factor = 1.0 + (((seed % 100) / 100.0) - 0.5) * 2 * pct
    return max(1, round(base * factor))


def _dow_factor(d: date) -> float:
    """Weekday multiplier matching real cafe patterns."""
    return {0: 0.95, 1: 0.88, 2: 0.90, 3: 0.95, 4: 1.10, 5: 1.20, 6: 0.85}[d.weekday()]


# ── Seed functions ───────────────────────────────────────────────────────────

def seed_site(conn) -> None:
    conn.execute(
        text("""
            INSERT INTO sites (site_id, name, square_location_id, timezone, active)
            VALUES (:sid, :name, :loc_id, 'Australia/Brisbane', TRUE)
            ON CONFLICT (site_id) DO UPDATE
              SET name = EXCLUDED.name,
                  square_location_id = EXCLUDED.square_location_id
        """),
        {"sid": DEMO_SITE_ID, "name": DEMO_SITE_NAME, "loc_id": DEMO_SQUARE_LOCATION_ID},
    )
    print(f"  site: {DEMO_SITE_NAME} ({DEMO_SITE_ID})")


def seed_profitability(conn, days: int = 30) -> None:
    count = 0
    for i in range(days, 0, -1):
        d = TODAY - timedelta(days=i)
        factor = _dow_factor(d)
        rev = round(AVG_REVENUE_CENTS * factor * (0.95 + (i % 7) * 0.01))
        labor = round(AVG_LABOR_CENTS * factor)
        drinks = round(AVG_DRINK_COUNT * factor)
        orders = round(AVG_ORDER_COUNT * factor)
        cogs = round(rev * 0.28)
        gross = rev - labor - cogs
        labor_pct = round((labor / rev) * 100, 2) if rev else 0
        conn.execute(
            text("""
                INSERT INTO daily_profitability
                  (site_id, profit_date, revenue_cents, labor_cost_cents, cogs_cents,
                   gross_profit_cents, order_count, drink_count, labor_pct,
                   labor_data_quality, computed_at)
                VALUES
                  (:sid, :d, :rev, :labor, :cogs, :gross, :orders, :drinks, :lpct,
                   'good', NOW())
                ON CONFLICT (site_id, profit_date) DO NOTHING
            """),
            {
                "sid": DEMO_SITE_ID, "d": d, "rev": rev, "labor": labor,
                "cogs": cogs, "gross": gross, "orders": orders, "drinks": drinks, "lpct": labor_pct,
            },
        )
        count += 1
    print(f"  profitability: {count} days seeded ({TODAY - timedelta(days=days)} → {TODAY - timedelta(days=1)})")


def seed_sales_history(conn, days: int = 30) -> None:
    count = 0
    for i in range(days, 0, -1):
        d = TODAY - timedelta(days=i)
        factor = _dow_factor(d)
        gross = round(AVG_REVENUE_CENTS * factor)
        conn.execute(
            text("""
                INSERT INTO daily_sales_history
                  (site_id, sale_date, gross_sales_cents, net_sales_cents, source)
                VALUES (:sid, :d, :gross, :net, 'demo')
                ON CONFLICT DO NOTHING
            """),
            {"sid": DEMO_SITE_ID, "d": d, "gross": gross, "net": round(gross * 0.95)},
        )
        count += 1
    print(f"  sales_history: {count} days seeded")


def seed_orders_today(conn, count: int = 5) -> None:
    """Seed a handful of completed orders for today so ingest diagnostics pass."""
    seeded = 0
    for i in range(count):
        order_id = f"demo-order-{TODAY.isoformat()}-{i:03d}"
        created = datetime(TODAY.year, TODAY.month, TODAY.day, 8 + i, 15, 0, tzinfo=TZ)
        closed = datetime(TODAY.year, TODAY.month, TODAY.day, 8 + i, 17, 0, tzinfo=TZ)
        payload = {
            "id": order_id,
            "state": "COMPLETED",
            "line_items": [
                {"name": "Latte", "quantity": "2", "total_money": {"amount": 1000}},
                {"name": "Cappuccino", "quantity": "1", "total_money": {"amount": 600}},
            ],
        }
        conn.execute(
            text("""
                INSERT INTO orders_raw (order_id, site_id, created_at, closed_at,
                                        total_money_cents, state, payload)
                VALUES (:oid, :sid, :ca, :cl, :total, 'COMPLETED', :payload)
                ON CONFLICT (order_id) DO NOTHING
            """),
            {
                "oid": order_id, "sid": DEMO_SITE_ID,
                "ca": created, "cl": closed,
                "total": 1600, "payload": json.dumps(payload),
            },
        )
        seeded += 1
    print(f"  orders_raw: {seeded} demo orders for {TODAY}")


def seed_roster_tomorrow(conn) -> None:
    """Seed 3 published shifts for tomorrow."""
    seeded = 0
    for staff in STAFF:
        start = datetime(TOMORROW.year, TOMORROW.month, TOMORROW.day,
                         staff["start_h"], 0, 0, tzinfo=TZ)
        end = datetime(TOMORROW.year, TOMORROW.month, TOMORROW.day,
                       staff["end_h"], 0, 0, tzinfo=TZ)
        hours = staff["end_h"] - staff["start_h"]
        conn.execute(
            text("""
                INSERT INTO deputy_rosters
                  (site_id, shift_date, start_time, end_time, employee_name,
                   total_hours, cost_dollars, is_published, is_open)
                VALUES (:sid, :d, :start, :end, :name, :hours, :cost, TRUE, FALSE)
                ON CONFLICT DO NOTHING
            """),
            {
                "sid": DEMO_SITE_ID, "d": TOMORROW, "start": start, "end": end,
                "name": staff["name"], "hours": hours, "cost": staff["cost"],
            },
        )
        seeded += 1
    total_cost = sum(s["cost"] for s in STAFF)
    print(f"  deputy_rosters: {seeded} shifts for {TOMORROW} (${total_cost:.0f} total labor)")


def seed_default_site_id_hint() -> None:
    """Print a hint to set DEFAULT_SITE_ID if not already in .env."""
    env_path = PROJECT_ROOT / ".env"
    if env_path.exists():
        content = env_path.read_text()
        if "DEFAULT_SITE_ID" not in content or f"DEFAULT_SITE_ID={DEMO_SITE_ID}" not in content:
            print(
                f"\n  HINT: Add this to your .env to avoid passing --site-id every time:\n"
                f"    DEFAULT_SITE_ID={DEMO_SITE_ID}"
            )


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    print(f"Seeding demo data for {DEMO_SITE_NAME} …")
    try:
        with engine.begin() as conn:
            seed_site(conn)
            seed_profitability(conn)
            seed_sales_history(conn)
            seed_orders_today(conn)
            seed_roster_tomorrow(conn)
        print("\nDone. Run `make tomorrow` to generate the report.")
        seed_default_site_id_hint()
        return 0
    except Exception as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        print(
            "Tip: ensure PostgreSQL is running and DATABASE_URL is correct in .env",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
