"""
Curiosity agenda builder for chat-led business learning.

This turns missing business logic and weak recommendation coverage into a
small set of targeted questions the assistant can ask over time.
"""

from __future__ import annotations

import re

from analysis.explanation_gaps import (
    detect_purchase_explanation_gaps,
    detect_wage_explanation_gaps,
)
from data.storage import list_operator_rules

from analysis.knowledge_gaps import detect_knowledge_gaps


def _normalize(raw: str | None) -> str:
    text = str(raw or "").strip().lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _format_cents_short(cents: int) -> str:
    dollars = abs(int(cents or 0)) / 100
    if dollars >= 1000:
        return f"${dollars:,.0f}/wk"
    return f"${dollars:,.0f}/wk"


def build_curiosity_agenda(
    site_id: str,
    *,
    top_items: list[dict] | None = None,
    inventory_alerts: list[dict] | None = None,
    inventory_usage_patterns: list[dict] | None = None,
    operator_rules: list[dict] | None = None,
    usage_rules: list[dict] | None = None,
    bottom_line_scorecard: dict | None = None,
    purchase_explanation_gaps: list[dict] | None = None,
    wage_explanation_gaps: list[dict] | None = None,
    limit: int = 5,
) -> list[dict]:
    if operator_rules is None:
        try:
            operator_rules = list_operator_rules(
                site_id,
                statuses=["confirmed"],
                active_only=True,
                limit=200,
            )
        except Exception:
            operator_rules = []

    agenda: list[dict] = []
    seen_questions: set[str] = set()

    def add_item(payload: dict) -> None:
        question = str(payload.get("question") or "").strip()
        if not question or question in seen_questions:
            return
        seen_questions.add(question)
        agenda.append(payload)

    base_gaps = detect_knowledge_gaps(
        site_id,
        top_items=top_items,
        inventory_alerts=inventory_alerts,
        operator_rules=operator_rules,
        usage_rules=usage_rules,
        limit=max(3, limit),
    )
    for gap in base_gaps:
        decision_unlocked = {
            "missing_recipe": "More accurate stock depletion and COGS attribution by product",
            "missing_recipe_variant": "More accurate stock depletion and margin logic by product variant",
            "missing_delivery_schedule": "Better order timing and stockout prevention",
            "missing_purchase_profile": "Usable supplier order recommendations instead of raw unit shortfalls",
            "missing_item_cost": "Real margin calculations in menu engineering and profitability reports",
        }.get(gap.get("gap_type"), "More reliable recommendations")
        add_item(
            {
                "agenda_type": "knowledge_gap",
                "priority": gap.get("priority") or "medium",
                "title": gap.get("title") or "Missing business logic",
                "question": gap.get("question") or "What rule is missing here?",
                "why_it_matters": gap.get("why_it_matters")
                or "This blocks recommendation quality.",
                "decision_unlocked": decision_unlocked,
                "source_gap_type": gap.get("gap_type"),
            }
        )

    if purchase_explanation_gaps is None:
        purchase_explanation_gaps = detect_purchase_explanation_gaps(
            site_id,
            operator_rules=operator_rules,
            limit=2,
        )
    for gap in purchase_explanation_gaps or []:
        add_item(
            {
                "agenda_type": "purchase_explanation",
                "priority": gap.get("priority") or "medium",
                "title": gap.get("title") or "Explain Xero purchase",
                "question": gap.get("question") or "What is this Xero expense for?",
                "why_it_matters": gap.get("why_it_matters")
                or "Unexplained purchases weaken cost attribution.",
                "decision_unlocked": gap.get("decision_unlocked")
                or "Better purchase classification and COGS memory",
                "reason_codes": gap.get("reason_codes") or [],
                "evidence": gap.get("evidence") or {},
            }
        )

    if wage_explanation_gaps is None:
        wage_explanation_gaps = detect_wage_explanation_gaps(
            site_id,
            bottom_line_scorecard=bottom_line_scorecard,
            limit=1,
        )
    for gap in wage_explanation_gaps or []:
        add_item(
            {
                "agenda_type": "labor_explanation",
                "priority": gap.get("priority") or "medium",
                "title": gap.get("title") or "Explain recent wage spike",
                "question": gap.get("question") or "Why did wages move above target?",
                "why_it_matters": gap.get("why_it_matters")
                or "Unexplained labor spikes weaken staffing recommendations.",
                "decision_unlocked": gap.get("decision_unlocked")
                or "Sharper diagnosis of labor inefficiency vs intentional spend",
                "evidence": gap.get("evidence") or {},
            }
        )

    rule_types = {
        str(rule.get("rule_type") or "").strip()
        for rule in (operator_rules or [])
        if str(rule.get("rule_type") or "").strip()
    }
    targets = ((bottom_line_scorecard or {}).get("targets") or {}).get("gaps") or {}
    primary_lever = (
        ((bottom_line_scorecard or {}).get("targets") or {}).get("primary_lever") or {}
    ).get("focus")
    labor_gap = int(targets.get("weekly_labor_reduction_needed_cents") or 0)

    if labor_gap > 0 and "staffing_constraint" not in rule_types:
        add_item(
            {
                "agenda_type": "workflow_learning",
                "priority": "high" if primary_lever == "labor_efficiency" else "medium",
                "title": "Missing workflow staffing rules",
                "question": (
                    "In your 2-person, 3-person, and 4-person workflows, which role can flex off "
                    "the floor first during quieter periods, and which role cannot be cut?"
                ),
                "why_it_matters": (
                    f"Labor is still above target by {_format_cents_short(labor_gap)}, but there are "
                    "no confirmed staffing constraints telling the system where efficiency cuts are safe."
                ),
                "decision_unlocked": "Sharper roster and workflow recommendations by daypart",
            }
        )

    if inventory_usage_patterns:
        for pattern in inventory_usage_patterns[:3]:
            item_name = pattern.get("item_name") or "inventory item"
            top_triggers = pattern.get("top_usage_triggers") or []
            if top_triggers:
                continue
            total = float(pattern.get("total_consumed_units") or 0)
            if total <= 0:
                continue
            add_item(
                {
                    "agenda_type": "consumption_mapping",
                    "priority": "medium",
                    "title": f"Consumption drivers unclear for {item_name}",
                    "question": f"Which sales items should reduce {item_name} from stock?",
                    "why_it_matters": (
                        f"{item_name} is clearly being consumed, but the system cannot yet attribute "
                        "that depletion back to specific sold products."
                    ),
                    "decision_unlocked": "Cleaner stock pattern learning and waste detection",
                }
            )

    # --- Pricing gaps: top-selling items with no recorded sell price ---
    pricing_items = {
        _normalize((r.get("payload") or {}).get("item_name"))
        for r in (operator_rules or [])
        if str(r.get("rule_type") or "") == "pricing"
    }
    for item in (top_items or [])[:8]:
        item_name = item.get("item") or item.get("item_name") or ""
        normalized = _normalize(item_name)
        sales_count = int(item.get("count") or 0)
        if not normalized or sales_count < 15 or normalized in pricing_items:
            continue
        add_item(
            {
                "agenda_type": "pricing_gap",
                "priority": "low",
                "title": f"Selling price unknown for {item_name}",
                "question": f"What do you charge for {item_name}?",
                "why_it_matters": (
                    f"{item_name} is one of your top sellers but has no price on record. "
                    "Without it, per-item margin calculations rely on revenue averages."
                ),
                "decision_unlocked": "Accurate per-item margin and product-mix recommendations",
            }
        )

    # --- COGS gaps: high-usage inventory with no supplier cost rule ---
    cost_items = {
        _normalize((r.get("payload") or {}).get("item_name"))
        for r in (operator_rules or [])
        if str(r.get("rule_type") or "") in {"cost_of_goods", "supplier_cost"}
    }
    for alert in (inventory_alerts or [])[:5]:
        item_name = alert.get("item_name") or ""
        normalized = _normalize(item_name)
        daily_usage = float(alert.get("daily_usage_units") or 0)
        if not normalized or daily_usage <= 0 or normalized in cost_items:
            continue
        add_item(
            {
                "agenda_type": "cogs_gap",
                "priority": "low",
                "title": f"Supplier cost unknown for {item_name}",
                "question": f"What do you currently pay per unit for {item_name}?",
                "why_it_matters": (
                    f"{item_name} is consumed daily but no unit cost is on record, "
                    "so COGS for this line can't be tracked accurately."
                ),
                "decision_unlocked": "Real COGS tracking and smarter reorder cost analysis",
            }
        )

    # --- Target gap: no profit target rule exists ---
    has_profit_target = any(
        str(r.get("rule_type") or "") == "profit_target" for r in (operator_rules or [])
    )
    if not has_profit_target and bottom_line_scorecard:
        revenue_cents = (bottom_line_scorecard.get("kpis") or {}).get("revenue_cents") or 0
        if revenue_cents:
            add_item(
                {
                    "agenda_type": "target_gap",
                    "priority": "low",
                    "title": "No profit target configured",
                    "question": (
                        "What net profit margin are you targeting — "
                        "and is there a weekly revenue goal you're working toward?"
                    ),
                    "why_it_matters": (
                        "Without a confirmed target the system uses industry defaults. "
                        "Setting your own goal focuses every recommendation on your actual objective."
                    ),
                    "decision_unlocked": "Personalised profitability scoring and lever prioritisation",
                }
            )

    # --- Weak BI hypotheses: surface as validation questions ---
    try:
        from analysis.business_intelligence import compute_business_intelligence
        bi = compute_business_intelligence(site_id, days=28)
        weak_hypotheses = [
            h for h in (bi.get("hypotheses") or [])
            if h.get("confidence_label") == "weak"
        ]
        _hyp_templates: dict[str, tuple[str, str]] = {
            "labour_trend": (
                "Labour cost pattern noticed",
                "We've noticed your labour percentage may have {direction}. Is that intentional, or worth investigating?",
            ),
            "avg_ticket_trend": (
                "Average order size shifting",
                "Your average order value appears to have {direction} recently. Is this a deliberate change?",
            ),
            "quiet_prep_window": (
                "Quiet window in the day",
                "Trade looks quieter around the early afternoon each day. How is that time being used currently?",
            ),
            "revenue_concentration_peak": (
                "Revenue concentrated in one window",
                "A large share of revenue is coming through a short window. Is your team set up to handle that pressure?",
            ),
            "dow_efficiency_gap": (
                "Staff efficiency varies by day",
                "Revenue per labour dollar looks lower on some days. Are certain days structured differently?",
            ),
            "modifier_alt_milk_penetration": (
                "High alternative milk demand",
                "Alternative milk demand looks significant. Do you have enough stock to cover it reliably?",
            ),
            "product_mix_shift": (
                "Product mix is shifting",
                "A few products are trending in different directions. Is this driven by a menu change or pricing?",
            ),
        }
        for h in weak_hypotheses[:2]:
            key = str(h.get("hypothesis_key") or "")
            base_key = next((k for k in _hyp_templates if key.startswith(k)), None)
            if not base_key:
                continue
            title, q_tmpl = _hyp_templates[base_key]
            evidence = h.get("evidence") or {}
            direction = str(evidence.get("direction") or "changing")
            question = q_tmpl.format(direction=direction)
            add_item(
                {
                    "agenda_type": "hypothesis_validation",
                    "priority": "low",
                    "title": title,
                    "question": question,
                    "why_it_matters": h.get("statement") or "The system has noticed a pattern but needs confirmation.",
                    "decision_unlocked": "Stronger confidence in insights and more reliable recommendations",
                    "source_hypothesis_key": key,
                }
            )
    except Exception:
        pass

    priority_rank = {"high": 0, "medium": 1, "low": 2}
    agenda.sort(
        key=lambda item: (
            priority_rank.get(str(item.get("priority") or "medium"), 9),
            item.get("title") or "",
        )
    )
    return agenda[: max(1, int(limit or 5))]
