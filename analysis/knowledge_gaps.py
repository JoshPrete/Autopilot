"""
Knowledge-gap detector for missing business logic and operating rules.

This powers chat curiosity by identifying the smallest set of missing
facts that materially weakens recommendations.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta

from sqlalchemy import text

from config.database import engine
from data.storage import (
    get_inventory_alerts,
    list_inventory_usage_rules,
    list_operator_rules,
)

logger = logging.getLogger("autopilot.knowledge_gaps")

TOP_ITEM_RECIPE_THRESHOLD = 20
MAX_GAPS = 5


def _normalize_label(raw: str | None) -> str:
    text_value = str(raw or "").strip().lower().replace("_", " ")
    text_value = re.sub(r"[^a-z0-9]+", " ", text_value)
    return " ".join(text_value.split())


def _get_top_items(site_id: str, days: int = 30, limit: int = 15) -> list[dict]:
    cutoff = date.today() - timedelta(days=max(1, int(days or 30)))
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT item_name, COUNT(*) AS cnt, "
                    "ROUND(AVG(workload_units)::numeric, 1) AS avg_wu "
                    "FROM order_items "
                    "WHERE site_id = :sid AND created_at >= :cutoff "
                    "GROUP BY item_name "
                    "ORDER BY cnt DESC LIMIT :lim"
                ),
                {"sid": site_id, "cutoff": cutoff, "lim": max(1, int(limit or 15))},
            )
            .mappings()
            .all()
        )

    return [
        {
            "item": row["item_name"],
            "count": int(row["cnt"]),
            "avg_workload": float(row["avg_wu"] or 0),
        }
        for row in rows
    ]


def _collect_modeled_triggers(operator_rules: list[dict], usage_rules: list[dict]) -> set[str]:
    triggers: set[str] = set()

    for rule in usage_rules:
        normalized = _normalize_label(rule.get("trigger_item_name"))
        if normalized:
            triggers.add(normalized)

    for rule in operator_rules:
        if rule.get("rule_type") != "recipe_definition":
            continue
        payload = rule.get("payload") or {}
        normalized = _normalize_label(payload.get("trigger_item_name"))
        if normalized:
            triggers.add(normalized)

    return triggers


def detect_knowledge_gaps(
    site_id: str,
    *,
    lookback_days: int = 30,
    top_items: list[dict] | None = None,
    inventory_alerts: list[dict] | None = None,
    operator_rules: list[dict] | None = None,
    usage_rules: list[dict] | None = None,
    limit: int = MAX_GAPS,
) -> list[dict]:
    """
    Detect high-signal missing business logic.

    Returns a ranked list of gaps with a suggested clarifying question.
    """
    if operator_rules is None:
        try:
            operator_rules = list_operator_rules(
                site_id,
                statuses=["confirmed"],
                active_only=True,
                limit=200,
            )
        except Exception as exc:
            logger.info("operator_rules unavailable for knowledge gaps: %s", exc)
            operator_rules = []

    if usage_rules is None:
        try:
            usage_rules = list_inventory_usage_rules(site_id, active_only=True)
        except Exception as exc:
            logger.info("inventory usage rules unavailable for knowledge gaps: %s", exc)
            usage_rules = []

    if top_items is None:
        try:
            top_items = _get_top_items(site_id, days=lookback_days, limit=15)
        except Exception as exc:
            logger.info("top items unavailable for knowledge gaps: %s", exc)
            top_items = []

    if inventory_alerts is None:
        try:
            inventory_alerts = get_inventory_alerts(
                site_id,
                lookback_days=lookback_days,
                include_ok=True,
            )
        except Exception as exc:
            logger.info("inventory alerts unavailable for knowledge gaps: %s", exc)
            inventory_alerts = []

    modeled_triggers = _collect_modeled_triggers(operator_rules, usage_rules)
    gaps: list[dict] = []
    seen_keys: set[str] = set()

    def add_gap(payload: dict) -> None:
        key = str(payload.get("key") or "").strip()
        if not key or key in seen_keys:
            return
        seen_keys.add(key)
        gaps.append(payload)

    for item in top_items:
        item_name = item.get("item")
        normalized = _normalize_label(item_name)
        sales_count = int(item.get("count") or 0)
        if not normalized or sales_count < TOP_ITEM_RECIPE_THRESHOLD:
            continue
        if normalized in modeled_triggers:
            continue
        add_gap(
            {
                "key": f"missing_recipe:{normalized}",
                "gap_type": "missing_recipe",
                "priority": "high",
                "title": f"Missing stock recipe for {item_name}",
                "question": f"What does {item_name} consume from stock?",
                "why_it_matters": (
                    f"{item_name} sold {sales_count} times in the last {lookback_days} days, "
                    "but there is no confirmed recipe or usage rule linking it to stock."
                ),
                "evidence": {
                    "item_name": item_name,
                    "sales_count": sales_count,
                    "avg_workload": float(item.get("avg_workload") or 0),
                },
            }
        )

    for alert in inventory_alerts:
        item_name = alert.get("item_name") or "Inventory item"
        normalized_item = _normalize_label(item_name)
        daily_usage = float(alert.get("daily_usage_units") or 0)
        status = str(alert.get("status") or "").strip()

        if not alert.get("schedule_source") and (
            daily_usage > 0 or status in {"low_stock", "out_of_stock", "stockout_before_delivery"}
        ):
            add_gap(
                {
                    "key": f"missing_schedule:{normalized_item}",
                    "gap_type": "missing_delivery_schedule",
                    "priority": (
                        "high"
                        if status in {"low_stock", "out_of_stock", "stockout_before_delivery"}
                        else "medium"
                    ),
                    "title": f"Missing delivery schedule for {item_name}",
                    "question": f"What days do you order or receive {item_name}?",
                    "why_it_matters": (
                        f"{item_name} is actively consumed at about {daily_usage:.1f} "
                        f"{alert.get('unit') or 'units'}/day, but there is no confirmed delivery or ordering schedule."
                    ),
                    "evidence": {
                        "item_name": item_name,
                        "daily_usage_units": round(daily_usage, 3),
                        "status": status,
                        "recommended_reorder_units": alert.get("recommended_reorder_units"),
                    },
                }
            )

        if (
            status in {"low_stock", "reorder_soon", "stockout_before_delivery", "out_of_stock"}
            and float(alert.get("recommended_reorder_units") or 0) > 0
            and not alert.get("order_profile_source")
        ):
            add_gap(
                {
                    "key": f"missing_pack_profile:{normalized_item}",
                    "gap_type": "missing_purchase_profile",
                    "priority": "medium",
                    "title": f"Missing purchase profile for {item_name}",
                    "question": (
                        f"How do you normally buy {item_name} "
                        "(pack size, supplier, and minimum order)?"
                    ),
                    "why_it_matters": (
                        f"{item_name} needs about {float(alert.get('recommended_reorder_units') or 0):.1f} "
                        f"{alert.get('unit') or 'units'}, but there is no pack-size or order-unit rule yet."
                    ),
                    "evidence": {
                        "item_name": item_name,
                        "status": status,
                        "recommended_reorder_units": alert.get("recommended_reorder_units"),
                    },
                }
            )

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    gaps.sort(
        key=lambda gap: (
            priority_rank.get(str(gap.get("priority") or "medium"), 9),
            gap.get("title") or "",
        )
    )
    return gaps[: max(1, int(limit or MAX_GAPS))]
