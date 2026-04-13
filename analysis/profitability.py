"""
Clubhouse Autopilot — Profitability Engine
Daily P&L computation, item-level margins, and labor efficiency metrics.

All COGS are estimates from config/constants.py DEFAULT_ITEM_COSTS
until the user updates them via the item_costs table.
"""

import logging
from decimal import Decimal, InvalidOperation
from datetime import date, timedelta

from analysis.sale_understanding import infer_sale_profile
from config.database import engine
from data.storage import (
    _build_virtual_inventory_usage_rules,
    _parse_modifier_tokens,
    _terms_match,
    get_item_costs,
    get_item_costs_detailed,
    list_inventory_items,
    list_inventory_usage_rules,
    list_operator_rules,
    seed_item_costs,
    store_daily_profitability,
)

logger = logging.getLogger("autopilot.profitability")


_HOT_DRINK_KEYS = {
    "espresso",
    "long_black",
    "latte",
    "cappuccino",
    "flat_white",
    "mocha",
    "matcha_complex",
    "babycino",
}

_RECIPE_COST_ALIASES = {
    "coffee_beans_g": [("coffee_beans_1kg", 1000.0), ("beans", 250.0)],
    "full_cream_milk_ml": [("full_cream_milk", 1000.0), ("milk", 1000.0)],
    "skim_milk_ml": [("skim_milk", 1000.0), ("full_cream_milk", 1000.0), ("milk", 1000.0)],
    "oat_milk_ml": [("oat_milk", 1000.0)],
    "soy_milk_ml": [("soy_milk", 1000.0)],
    "almond_milk_ml": [("almond_milk", 1000.0)],
    "cup_12oz": [("cup_12oz", 1.0), ("eco_cup_16oz", 1.0)],
    "lid_90mm": [("lid_90mm", 1.0), ("cup_lid_travel", 1.0), ("cup_lid_sip", 1.0)],
}

_RECIPE_UNIT_COST_FALLBACKS = {
    "coffee_beans_g": {"cost_per_unit": 2.5, "label": "estimated beans cost"},
    "full_cream_milk_ml": {"cost_per_unit": 0.22, "label": "estimated full cream milk cost"},
    "skim_milk_ml": {"cost_per_unit": 0.22, "label": "estimated skim milk cost"},
    "oat_milk_ml": {"cost_per_unit": 0.26, "label": "estimated oat milk cost"},
    "soy_milk_ml": {"cost_per_unit": 0.35, "label": "estimated soy milk cost"},
    "almond_milk_ml": {"cost_per_unit": 0.35, "label": "estimated almond milk cost"},
    "cup_12oz": {"cost_per_unit": 8.0, "label": "estimated 12oz cup cost"},
    "lid_90mm": {"cost_per_unit": 3.0, "label": "estimated 90mm lid cost"},
}

_COGS_SOURCE_LABELS = {
    "recipe": "Recipe-based",
    "recipe_estimate": "Recipe-based estimate",
    "xero_flat": "Flat Xero cost",
    "default_flat": "Flat default cost",
    "flat": "Flat item cost",
    "unknown": "Unknown cost basis",
}


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


def _derive_sale_profile(item_name: str | None, score_key: str) -> dict:
    profile = infer_sale_profile(item_name)
    if not profile.get("family"):
        if score_key == "iced_latte":
            profile["family"] = "latte"
        elif score_key in _HOT_DRINK_KEYS:
            profile["family"] = score_key

    if not profile.get("serve_temperature"):
        if score_key == "iced_latte":
            profile["serve_temperature"] = "iced"
        elif score_key in _HOT_DRINK_KEYS:
            profile["serve_temperature"] = "hot"

    variant_parts = []
    for value in (
        profile.get("size_label"),
        profile.get("serve_temperature"),
        profile.get("service_mode"),
    ):
        if value:
            variant_parts.append(value)
    profile["variant_key"] = "_".join(variant_parts) if variant_parts else None
    return profile


def _rule_specificity(candidate_profile: dict) -> int:
    return sum(
        1
        for key in ("size_label", "size_oz", "serve_temperature", "service_mode")
        if candidate_profile.get(key) not in (None, "")
    )


def _match_rule_rank(
    item_name: str,
    score_key: str,
    item_profile: dict,
    rule: dict,
    rule_profiles: dict[str, dict],
) -> int:
    trigger_name = str(rule.get("trigger_item_name") or "").strip()
    if not trigger_name:
        return 0

    trigger_key = trigger_name.lower()
    item_key = str(item_name or "").strip().lower()
    if trigger_key == item_key:
        return 100

    candidate = rule_profiles.get(str(rule.get("rule_id")))
    if not candidate:
        return 0

    candidate_score_key = candidate.get("score_key")
    candidate_profile = candidate.get("sale_profile") or {}
    specificity = _rule_specificity(candidate_profile)

    if candidate_score_key and candidate_score_key == score_key:
        compatible = True
        for attr in ("size_label", "size_oz", "serve_temperature", "service_mode"):
            candidate_value = candidate_profile.get(attr)
            item_value = item_profile.get(attr)
            if candidate_value in (None, ""):
                continue
            if item_value in (None, ""):
                compatible = False
                break
            if candidate_value != item_value:
                compatible = False
                break
        if compatible:
            return 80 + specificity

    candidate_family = candidate_profile.get("family")
    item_family = item_profile.get("family")
    if candidate_family and item_family and candidate_family == item_family:
        compatible = True
        for attr in ("size_label", "size_oz", "serve_temperature", "service_mode"):
            candidate_value = candidate_profile.get(attr)
            item_value = item_profile.get(attr)
            if candidate_value in (None, ""):
                continue
            if item_value in (None, ""):
                compatible = False
                break
            if candidate_value != item_value:
                compatible = False
                break
        if compatible:
            return 60 + specificity

    return 0


def _build_recipe_context(site_id: str) -> dict:
    items = list_inventory_items(site_id, active_only=True)
    usage_rules = list_inventory_usage_rules(site_id, active_only=True)
    operator_rules = list_operator_rules(
        site_id,
        statuses=["confirmed"],
        active_only=True,
        limit=500,
    )
    if operator_rules:
        usage_rules = usage_rules + _build_virtual_inventory_usage_rules(items, usage_rules, operator_rules)

    try:
        cost_records = {row["score_key"]: row for row in get_item_costs_detailed(site_id)}
    except Exception:
        cost_records = {}
    items_by_id = {str(item.get("inventory_item_id")): item for item in items}

    rule_profiles: dict[str, dict] = {}
    for rule in usage_rules:
        trigger_name = rule.get("trigger_item_name")
        if not trigger_name:
            continue
        try:
            from data.processing import resolve_item_key

            trigger_score_key, _category = resolve_item_key(trigger_name)
        except Exception:
            trigger_score_key = None
        rule_profiles[str(rule.get("rule_id"))] = {
            "score_key": trigger_score_key,
            "sale_profile": _derive_sale_profile(trigger_name, trigger_score_key or ""),
        }

    return {
        "items_by_id": items_by_id,
        "rules": usage_rules,
        "rule_profiles": rule_profiles,
        "cost_records": cost_records,
    }


def _resolve_component_unit_cost(item: dict, cost_records: dict[str, dict]) -> dict | None:
    score_key = str(item.get("score_key") or "")
    aliases = _RECIPE_COST_ALIASES.get(score_key, [(score_key, 1.0)])
    for alias_key, divisor in aliases:
        record = cost_records.get(alias_key)
        if not record:
            continue
        return {
            "cost_per_unit": float(record.get("cost_cents") or 0) / max(1.0, float(divisor)),
            "source": record.get("source") or "default",
            "basis": alias_key,
        }

    fallback = _RECIPE_UNIT_COST_FALLBACKS.get(score_key)
    if not fallback:
        return None
    return {
        "cost_per_unit": float(fallback["cost_per_unit"]),
        "source": "estimated",
        "basis": fallback["label"],
    }


def _build_flat_cost_detail(score_key: str, item_costs: dict[str, int], cost_records: dict[str, dict]) -> dict:
    unit_cogs = int(item_costs.get(score_key, 0) or 0)
    record = cost_records.get(score_key)
    flat_source = (record or {}).get("source") or ("default" if unit_cogs else "unknown")
    if flat_source == "xero":
        source_key = "xero_flat"
    elif flat_source == "default":
        source_key = "default_flat"
    elif flat_source == "unknown":
        source_key = "unknown"
    else:
        source_key = "flat"
    return {
        "cogs_cents": unit_cogs,
        "cogs_source": source_key,
        "cogs_source_label": _COGS_SOURCE_LABELS[source_key],
        "cogs_detail": f"Flat item cost on `{score_key}` ({flat_source}).",
        "cogs_components": [],
    }


def _estimate_recipe_cost(
    item_name: str,
    score_key: str,
    modifiers,
    recipe_context: dict,
) -> dict | None:
    items_by_id = recipe_context["items_by_id"]
    rules = recipe_context["rules"]
    rule_profiles = recipe_context["rule_profiles"]
    cost_records = recipe_context["cost_records"]
    if not items_by_id or not rules:
        return None

    item_profile = _derive_sale_profile(item_name, score_key)
    tokens = _parse_modifier_tokens(modifiers)
    best_rules_by_item: dict[str, tuple[int, int, dict]] = {}

    for rule in rules:
        item_id = str(rule.get("inventory_item_id") or "")
        if item_id not in items_by_id:
            continue
        if not _terms_match(tokens, rule.get("required_modifier_terms"), require=True):
            continue
        if not _terms_match(tokens, rule.get("excluded_modifier_terms"), require=False):
            continue

        rank = _match_rule_rank(item_name, score_key, item_profile, rule, rule_profiles)
        if rank <= 0:
            continue

        current = best_rules_by_item.get(item_id)
        priority = int(rule.get("priority") or 100)
        if current is None or rank > current[0] or (rank == current[0] and priority < current[1]):
            best_rules_by_item[item_id] = (rank, priority, rule)

    if not best_rules_by_item:
        return None

    components = []
    total_cogs = 0
    has_estimate = False

    for item_id, (_rank, _priority, rule) in best_rules_by_item.items():
        inventory_item = items_by_id[item_id]
        quantity = float(rule.get("units_per_sale") or 0)
        if quantity <= 0:
            continue
        unit_cost = _resolve_component_unit_cost(inventory_item, cost_records)
        if not unit_cost:
            return None
        component_cost = int(round(quantity * unit_cost["cost_per_unit"]))
        total_cogs += component_cost
        if unit_cost["source"] == "estimated":
            has_estimate = True
        components.append(
            {
                "item_name": inventory_item.get("item_name"),
                "quantity": quantity,
                "unit": inventory_item.get("unit") or "units",
                "cost_cents": component_cost,
                "source": unit_cost["source"],
                "basis": unit_cost["basis"],
            }
        )

    if not components:
        return None

    components.sort(key=lambda component: component["cost_cents"], reverse=True)
    source_key = "recipe_estimate" if has_estimate else "recipe"
    detail_bits = [
        f"{component['quantity']:g}{component['unit']} {component['item_name']}"
        for component in components
    ]
    detail = "Recipe-based from " + ", ".join(detail_bits) + "."
    if has_estimate:
        detail += " Some ingredients still use fallback estimates."

    return {
        "cogs_cents": total_cogs,
        "cogs_source": source_key,
        "cogs_source_label": _COGS_SOURCE_LABELS[source_key],
        "cogs_detail": detail,
        "cogs_components": components,
    }


def _assess_labor_data_quality(
    labor_cost_cents: int,
    labor_hours: float,
    shift_count: int,
    max_hourly_rate: float | None,
    median_daily_labor_cents: int | None,
    revenue_cents: int,
) -> tuple[int, str, list[str]]:
    """
    Apply guardrails to Deputy-derived labor and return:
      (possibly adjusted labor_cost_cents, quality_flag, issues)
    """
    issues: list[str] = []
    adjusted = labor_cost_cents

    if shift_count <= 0 or labor_hours <= 0:
        issues.append("missing_roster")

    if max_hourly_rate is not None and max_hourly_rate > 70:
        issues.append("implausible_hourly_rate")

    if labor_hours > 32:
        issues.append("excessive_total_shift_hours")

    if median_daily_labor_cents and labor_cost_cents > int(median_daily_labor_cents * 2.2):
        issues.append("labor_cost_outlier_vs_history")
        adjusted = min(adjusted, round(median_daily_labor_cents * 1.25))
        if adjusted < labor_cost_cents:
            issues.append("labor_cost_capped_to_baseline")

    labor_pct = (adjusted / revenue_cents * 100) if revenue_cents > 0 else 0
    if labor_pct > 70:
        issues.append("extreme_labor_pct")

    if "missing_roster" in issues:
        quality = "missing"
    elif issues:
        quality = "suspect"
    else:
        quality = "trusted"
    return adjusted, quality, issues


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
        rev_row = (
            conn.execute(
                _text(
                    "SELECT COUNT(*) AS order_count, "
                    "COALESCE(SUM(total_money_cents), 0) AS total_revenue "
                    "FROM orders_raw "
                    "WHERE site_id = :sid "
                    "AND DATE(closed_at) = :d "
                    "AND state = 'COMPLETED'"
                ),
                {"sid": site_id, "d": target_date},
            )
            .mappings()
            .first()
        )

        order_count = int(rev_row["order_count"])
        revenue_cents = int(rev_row["total_revenue"])

        if order_count == 0:
            logger.info("No orders for %s — skipping profitability", target_date)
            return None

        # 2. Labor cost and hours from deputy_rosters (deduped + guarded)
        labor_row = (
            conn.execute(
                _text(
                    """
                WITH shifts AS (
                    SELECT DISTINCT ON (
                        COALESCE(
                            deputy_id::text,
                            CONCAT_WS('|', employee_id::text, employee_name, start_time::text, end_time::text)
                        )
                    )
                        total_hours,
                        cost_dollars,
                        deputy_id
                    FROM deputy_rosters
                    WHERE site_id = :sid
                      AND shift_date = :d
                      AND COALESCE(is_open, FALSE) = FALSE
                    ORDER BY
                        COALESCE(
                            deputy_id::text,
                            CONCAT_WS('|', employee_id::text, employee_name, start_time::text, end_time::text)
                        ),
                        created_at DESC
                )
                SELECT
                    COALESCE(SUM(GREATEST(cost_dollars, 0)), 0) AS total_cost,
                    COALESCE(SUM(GREATEST(total_hours, 0)), 0) AS total_hours,
                    COUNT(*) AS shift_count,
                    MAX(
                        CASE WHEN total_hours > 0
                             THEN cost_dollars / NULLIF(total_hours, 0)
                             ELSE NULL END
                    ) AS max_hourly_rate
                FROM shifts
                """
                ),
                {"sid": site_id, "d": target_date},
            )
            .mappings()
            .first()
        )

        labor_cost_cents_raw = round(float(labor_row["total_cost"]) * 100)
        labor_hours = float(labor_row["total_hours"])
        shift_count = int(labor_row["shift_count"] or 0)
        max_hourly_rate = (
            float(labor_row["max_hourly_rate"])
            if labor_row["max_hourly_rate"] is not None
            else None
        )

        median_row = (
            conn.execute(
                _text(
                    """
                WITH daily AS (
                    SELECT
                        shift_date,
                        COALESCE(SUM(GREATEST(cost_dollars, 0)), 0) * 100 AS labor_cents
                    FROM deputy_rosters
                    WHERE site_id = :sid
                      AND shift_date BETWEEN :start_d AND :end_d
                      AND shift_date <> :d
                      AND COALESCE(is_open, FALSE) = FALSE
                    GROUP BY shift_date
                )
                SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY labor_cents) AS median_labor_cents
                FROM daily
                """
                ),
                {
                    "sid": site_id,
                    "d": target_date,
                    "start_d": target_date - timedelta(days=28),
                    "end_d": target_date - timedelta(days=1),
                },
            )
            .mappings()
            .first()
        )
        median_daily_labor_cents = (
            int(median_row["median_labor_cents"])
            if median_row and median_row.get("median_labor_cents") is not None
            else None
        )

        labor_cost_cents, labor_data_quality, labor_data_issues = _assess_labor_data_quality(
            labor_cost_cents=labor_cost_cents_raw,
            labor_hours=labor_hours,
            shift_count=shift_count,
            max_hourly_rate=max_hourly_rate,
            median_daily_labor_cents=median_daily_labor_cents,
            revenue_cents=revenue_cents,
        )

        # Add amortised owner salary ($100k/365 per day) after quality adjustment.
        from config.constants import OWNER_DAILY_SALARY_CENTS

        labor_cost_cents += OWNER_DAILY_SALARY_CENTS

        # 3. Item counts and COGS from order_items
        # We need the score_key for each item to look up COGS.
        # order_items stores item_name; we resolve via processing.resolve_item_key()
        items_rows = (
            conn.execute(
                _text(
                    "SELECT item_name, COUNT(*) AS qty "
                    "FROM order_items "
                    "WHERE site_id = :sid AND DATE(created_at) = :d "
                    "GROUP BY item_name"
                ),
                {"sid": site_id, "d": target_date},
            )
            .mappings()
            .all()
        )

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
    cost_per_drink = (
        round((total_cogs_cents + labor_cost_cents) / drink_count) if drink_count > 0 else None
    )

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
        "labor_data_quality": labor_data_quality,
        "labor_data_issues": labor_data_issues,
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
    applies recipe-driven COGS where possible, and falls back to flat item_costs
    where recipe coverage is missing.

    Returns list sorted by total profit contribution (most profitable first):
        [{
            item, score_key, qty, avg_price_cents, cogs_cents, margin_pct,
            total_profit_cents, cogs_source, cogs_source_label, cogs_detail,
            cogs_components
        }, ...]
    """
    seed_item_costs(site_id)
    item_costs = get_item_costs(site_id)
    recipe_context = _build_recipe_context(site_id)

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

    # Accumulate per-item: {score_key: {name, qty, total_revenue_cents, total_cogs_cents}}
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
            modifiers = li.get("modifiers")

            score_key, category = resolve_item_key(name)
            recipe_cost = _estimate_recipe_cost(name, score_key, modifiers, recipe_context)
            if recipe_cost is None:
                recipe_cost = _build_flat_cost_detail(
                    score_key,
                    item_costs=item_costs,
                    cost_records=recipe_context["cost_records"],
                )
            unit_cogs = int(recipe_cost["cogs_cents"])

            if score_key not in item_data:
                item_data[score_key] = {
                    "name": name,
                    "category": category,
                    "qty": 0,
                    "total_revenue_cents": 0,
                    "total_cogs_cents": 0,
                    "cost_source_totals": {},
                    "cost_detail_by_source": {},
                    "component_totals": {},
                }

            item_data[score_key]["qty"] += qty
            item_data[score_key]["total_revenue_cents"] += item_revenue
            item_data[score_key]["total_cogs_cents"] += unit_cogs * qty
            item_data[score_key]["cost_source_totals"][recipe_cost["cogs_source"]] = (
                int(item_data[score_key]["cost_source_totals"].get(recipe_cost["cogs_source"]) or 0)
                + qty
            )
            item_data[score_key]["cost_detail_by_source"].setdefault(
                recipe_cost["cogs_source"],
                recipe_cost["cogs_detail"],
            )
            for component in recipe_cost.get("cogs_components") or []:
                component_key = component["item_name"]
                existing_component = item_data[score_key]["component_totals"].setdefault(
                    component_key,
                    {
                        "item_name": component["item_name"],
                        "unit": component["unit"],
                        "source": component["source"],
                        "basis": component["basis"],
                        "quantity": 0.0,
                        "cost_cents": 0,
                    },
                )
                existing_component["quantity"] += float(component["quantity"]) * qty
                existing_component["cost_cents"] += int(component["cost_cents"]) * qty

    # Build margin report
    results = []
    for score_key, data in item_data.items():
        qty = data["qty"]
        if qty == 0:
            continue

        total_rev = data["total_revenue_cents"]
        avg_price = round(total_rev / qty)
        total_cogs = int(data.get("total_cogs_cents") or 0)
        unit_cogs = round(total_cogs / qty)
        total_profit = total_rev - total_cogs
        margin_pct = round((total_rev - total_cogs) / total_rev * 100, 1) if total_rev > 0 else 0
        cost_source_totals = data.get("cost_source_totals") or {}
        if cost_source_totals:
            dominant_source = max(cost_source_totals.items(), key=lambda pair: pair[1])[0]
        else:
            dominant_source = "unknown"
        component_totals = list((data.get("component_totals") or {}).values())
        component_totals.sort(key=lambda component: component["cost_cents"], reverse=True)

        results.append(
            {
                "item": data["name"],
                "score_key": score_key,
                "category": data["category"],
                "qty": qty,
                "avg_price_cents": avg_price,
                "cogs_cents": unit_cogs,
                "margin_pct": margin_pct,
                "total_profit_cents": total_profit,
                "cogs_source": dominant_source,
                "cogs_source_label": _COGS_SOURCE_LABELS.get(dominant_source, "Cost basis"),
                "cogs_detail": (
                    data.get("cost_detail_by_source", {}).get(dominant_source)
                    or "Cost basis unavailable."
                ),
                "cogs_components": component_totals,
            }
        )

    # Sort by total profit contribution (highest first)
    results.sort(key=lambda x: x["total_profit_cents"], reverse=True)
    return results
