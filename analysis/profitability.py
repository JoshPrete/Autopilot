"""
Clubhouse Autopilot — Profitability Engine
Daily P&L computation, item-level margins, and labor efficiency metrics.

All COGS are estimates from config/constants.py DEFAULT_ITEM_COSTS
until the user updates them via the item_costs table.
"""

import logging
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta

from config.database import engine
from data.storage import (
    get_item_costs,
    seed_item_costs,
    store_daily_profitability,
)

logger = logging.getLogger("autopilot.profitability")


def _text(sql: str):
    from sqlalchemy import text
    return text(sql)


def _parse_quantity(raw_qty) -> int:
    """Parse Square quantity values (e.g. '1', '1.000000') into an int."""
    if raw_qty is None:
        return 1
    if isinstance(raw_qty, int):
        return max(raw_qty, 0)
    if isinstance(raw_qty, float):
        return max(int(raw_qty), 0)
    try:
        value = Decimal(str(raw_qty))
    except (InvalidOperation, TypeError, ValueError):
        return 1
    if value <= 0:
        return 0
    return int(value)


def compute_daily_profitability(site_id: str, target_date: date) -> dict | None:
    """
    Compute and store a full daily P&L for a single date.

    1. Sum revenue from orders_raw.total_money_cents
    2. Sum labor from deputy_rosters.cost_dollars
    3. Count items from order_items, look up COGS per score_key
    4. Compute: gross_profit, net_profit, labor_%, rev/hour, cost/drink
    5. Store to daily_profitability

    Returns the metrics dict, or None if no revenue data.
    """
    # Ensure COGS are seeded
    seed_item_costs(site_id)
    item_costs = get_item_costs(site_id)

    with engine.connect() as conn:
        # 1. Revenue for the date
        rev_row = conn.execute(
            _text(
                "SELECT COUNT(*) AS order_count, "
                "COALESCE(SUM(total_money_cents), 0) AS total_revenue "
                "FROM orders_raw "
                "WHERE site_id = :sid "
                "AND DATE(closed_at) = :d "
                "AND state = 'COMPLETED'"
            ),
            {"sid": site_id, "d": target_date},
        ).mappings().first()

        order_count = int(rev_row["order_count"])
        revenue_cents = int(rev_row["total_revenue"])

        if order_count == 0:
            logger.info("No orders for %s — skipping profitability", target_date)
            return None

        # 2. Labor cost and hours from deputy_rosters
        labor_row = conn.execute(
            _text(
                "SELECT COALESCE(SUM(cost_dollars), 0) AS total_cost, "
                "COALESCE(SUM(total_hours), 0) AS total_hours "
                "FROM deputy_rosters "
                "WHERE site_id = :sid AND shift_date = :d"
            ),
            {"sid": site_id, "d": target_date},
        ).mappings().first()

        labor_cost_dollars = float(labor_row["total_cost"])
        labor_cost_cents = round(labor_cost_dollars * 100)
        labor_hours = float(labor_row["total_hours"])

        # 3. Item counts and COGS from order_items
        # We need the score_key for each item to look up COGS.
        # order_items stores item_name; we resolve via processing.resolve_item_key()
        items_rows = conn.execute(
            _text(
                "SELECT item_name, COUNT(*) AS qty "
                "FROM order_items "
                "WHERE site_id = :sid AND DATE(created_at) = :d "
                "GROUP BY item_name"
            ),
            {"sid": site_id, "d": target_date},
        ).mappings().all()

    # Resolve item names to score_keys and compute COGS
    from data.processing import resolve_item_key

    total_cogs_cents = 0
    total_items = 0
    drink_count = 0

    for row in items_rows:
        item_name = row["item_name"]
        qty = int(row["qty"])
        total_items += qty

        score_key, category = resolve_item_key(item_name or "unknown")
        unit_cost = item_costs.get(score_key, 0)
        total_cogs_cents += unit_cost * qty

        if category == "drink":
            drink_count += qty

    # 4. Compute derived metrics
    gross_profit_cents = revenue_cents - total_cogs_cents
    net_profit_cents = revenue_cents - total_cogs_cents - labor_cost_cents

    labor_pct = round(labor_cost_cents / revenue_cents * 100, 2) if revenue_cents > 0 else 0
    revenue_per_labor_hour = round(revenue_cents / labor_hours) if labor_hours > 0 else None
    cost_per_drink = round(
        (total_cogs_cents + labor_cost_cents) / drink_count
    ) if drink_count > 0 else None

    metrics = {
        "revenue_cents": revenue_cents,
        "labor_cost_cents": labor_cost_cents,
        "cogs_cents": total_cogs_cents,
        "gross_profit_cents": gross_profit_cents,
        "net_profit_cents": net_profit_cents,
        "order_count": order_count,
        "item_count": total_items,
        "drink_count": drink_count,
        "labor_hours": labor_hours,
        "revenue_per_labor_hour": revenue_per_labor_hour,
        "cost_per_drink": cost_per_drink,
        "labor_pct": labor_pct,
    }

    # 5. Store
    store_daily_profitability(site_id, target_date, metrics)

    logger.info(
        "Profitability %s: rev=$%.2f, COGS=$%.2f, labor=$%.2f, net=$%.2f, labor%%=%.1f%%",
        target_date,
        revenue_cents / 100,
        total_cogs_cents / 100,
        labor_cost_cents / 100,
        net_profit_cents / 100,
        labor_pct,
    )

    return metrics


def compute_item_margins(site_id: str, days: int = 14) -> list[dict]:
    """
    Compute per-item profitability over the last N days.

    Extracts per-item revenue from raw order payloads (line_items->total_money->amount),
    joins with item_costs for COGS, and returns margin analysis.

    Returns list sorted by total profit contribution (most profitable first):
        [{item, score_key, qty, avg_price_cents, cogs_cents, margin_pct, total_profit_cents}, ...]
    """
    seed_item_costs(site_id)
    item_costs = get_item_costs(site_id)

    cutoff = date.today() - timedelta(days=days)

    with engine.connect() as conn:
        # Pull line-item detail from raw order payloads
        rows = conn.execute(
            _text(
                "SELECT payload "
                "FROM orders_raw "
                "WHERE site_id = :sid AND DATE(closed_at) >= :cutoff "
                "AND state = 'COMPLETED'"
            ),
            {"sid": site_id, "cutoff": cutoff},
        ).all()

    import json
    from data.processing import resolve_item_key

    # Accumulate per-item: {score_key: {name, qty, total_revenue_cents}}
    item_data: dict[str, dict] = {}

    for row in rows:
        payload = row[0]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (json.JSONDecodeError, TypeError):
                continue
        if not isinstance(payload, dict):
            continue

        for li in payload.get("line_items", []):
            name = li.get("name", "Unknown")
            # Revenue per line item (Square stores in cents)
            total_money = li.get("total_money", {})
            item_revenue = int(total_money.get("amount", 0)) if isinstance(total_money, dict) else 0
            qty = _parse_quantity(li.get("quantity", 1))

            score_key, category = resolve_item_key(name)

            if score_key not in item_data:
                item_data[score_key] = {
                    "name": name,
                    "category": category,
                    "qty": 0,
                    "total_revenue_cents": 0,
                }

            item_data[score_key]["qty"] += qty
            item_data[score_key]["total_revenue_cents"] += item_revenue

    # Build margin report
    results = []
    for score_key, data in item_data.items():
        qty = data["qty"]
        if qty == 0:
            continue

        total_rev = data["total_revenue_cents"]
        avg_price = round(total_rev / qty)
        unit_cogs = item_costs.get(score_key, 0)
        total_cogs = unit_cogs * qty
        total_profit = total_rev - total_cogs
        margin_pct = round((total_rev - total_cogs) / total_rev * 100, 1) if total_rev > 0 else 0

        results.append({
            "item": data["name"],
            "score_key": score_key,
            "category": data["category"],
            "qty": qty,
            "avg_price_cents": avg_price,
            "cogs_cents": unit_cogs,
            "margin_pct": margin_pct,
            "total_profit_cents": total_profit,
        })

    # Sort by total profit contribution (highest first)
    results.sort(key=lambda x: x["total_profit_cents"], reverse=True)
    return results
