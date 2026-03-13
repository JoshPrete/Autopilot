"""
Explanation-gap detectors for purchases and wage spikes.

These identify places where the system can see a cost or labor movement
but still cannot explain what caused it.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta

from config.constants import LABOR_PCT_TARGET_HIGH
from data.storage import (
    get_bottom_line_scorecard,
    get_daily_profitability,
    list_operator_rules,
    list_xero_review_queue,
)

PURPOSE_REASON_CODES = {"UNMAPPED", "LOW_CONFIDENCE", "PENDING_APPROVAL"}
COST_SPIKE_REASON_CODES = {"OUTLIER_COST", "EXCESSIVE_DELTA"}


def _normalize(raw: str | None) -> str:
    return " ".join(str(raw or "").strip().lower().replace("_", " ").split())


def _format_cents_short(cents: int) -> str:
    dollars = abs(int(cents or 0)) / 100
    if dollars >= 1000:
        return f"${dollars:,.0f}/wk"
    return f"${dollars:,.0f}/wk"


def _safe_payload(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    if isinstance(value, dict):
        return value
    return {}


def _known_purchase_keys(operator_rules: list[dict] | None) -> set[str]:
    keys: set[str] = set()
    for rule in operator_rules or []:
        if str(rule.get("rule_type") or "").strip() != "purchase_profile":
            continue
        payload = rule.get("payload") or {}
        subject = _normalize(payload.get("subject"))
        supplier = _normalize(payload.get("supplier_name"))
        if subject:
            keys.add(subject)
        if supplier:
            keys.add(supplier)
    return keys


def detect_purchase_explanation_gaps(
    site_id: str,
    *,
    review_items: list[dict] | None = None,
    operator_rules: list[dict] | None = None,
    since_days: int = 45,
    limit: int = 2,
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

    if review_items is None:
        try:
            review_items = list_xero_review_queue(
                site_id,
                since=datetime.now() - timedelta(days=max(1, int(since_days or 45))),
                queue_status="open",
                limit=200,
            )
        except Exception:
            review_items = []

    known_keys = _known_purchase_keys(operator_rules)
    grouped: dict[tuple[str, str, str], dict] = {}

    for item in review_items or []:
        reason_code = str(item.get("reason_code") or "").strip().upper()
        if reason_code not in PURPOSE_REASON_CODES | COST_SPIKE_REASON_CODES:
            continue

        supplier = str(item.get("supplier") or "Unknown supplier").strip()
        line_description = str(item.get("line_description") or "Xero bill line").strip()
        normalized_supplier = _normalize(supplier)
        normalized_description = _normalize(line_description)
        if (
            reason_code in PURPOSE_REASON_CODES
            and normalized_description in known_keys
            or normalized_supplier in known_keys
        ):
            continue

        bucket = "purpose" if reason_code in PURPOSE_REASON_CODES else "cost_spike"
        key = (bucket, normalized_supplier, normalized_description)
        group = grouped.setdefault(
            key,
            {
                "bucket": bucket,
                "supplier": supplier,
                "line_description": line_description,
                "reasons": set(),
                "occurrences": 0,
                "line_total_cents": 0,
                "latest_bill_date": None,
                "suggested_score_key": item.get("suggested_score_key"),
                "review_ids": [],
                "payloads": [],
            },
        )
        group["reasons"].add(reason_code)
        group["occurrences"] += 1
        group["line_total_cents"] += int(round(float(item.get("line_total") or 0) * 100))
        if item.get("bill_date"):
            current_bill_date = str(item.get("bill_date"))
            if not group["latest_bill_date"] or current_bill_date > group["latest_bill_date"]:
                group["latest_bill_date"] = current_bill_date
        if item.get("review_id") is not None:
            group["review_ids"].append(int(item["review_id"]))
        payload = _safe_payload(item.get("payload"))
        if payload:
            group["payloads"].append(payload)

    ranked = sorted(
        grouped.values(),
        key=lambda row: (
            0 if row["bucket"] == "cost_spike" else 1,
            -int(row["occurrences"]),
            -abs(int(row["line_total_cents"])),
            row["supplier"].lower(),
            row["line_description"].lower(),
        ),
    )

    gaps: list[dict] = []
    for group in ranked[: max(1, int(limit or 2))]:
        reason_codes = sorted(group["reasons"])
        title: str
        question: str
        why_it_matters: str
        priority = "high" if group["bucket"] == "cost_spike" or group["occurrences"] >= 2 else "medium"
        if group["bucket"] == "cost_spike":
            title = f"Explain Xero cost spike for {group['line_description']}"
            question = (
                f"Why did `{group['line_description']}` from {group['supplier']} move so sharply in cost "
                "— supplier change, pack-size change, or a one-off invoice?"
            )
            why_it_matters = (
                f"Xero flagged `{group['line_description']}` from {group['supplier']} for "
                f"{', '.join(reason_codes)}. Until that change is explained, COGS and reorder cost logic stay noisy."
            )
            decision_unlocked = "More reliable cost guardrails and better supplier-cost memory"
        else:
            title = f"Explain recurring Xero purchase for {group['line_description']}"
            question = (
                f"What is `{group['line_description']}` from {group['supplier']} for in the business "
                "— stock, packaging, maintenance, marketing, or overhead?"
            )
            why_it_matters = (
                f"Xero has {group['occurrences']} open review item(s) for `{group['line_description']}` "
                f"from {group['supplier']}. Until the purpose is known, the system cannot classify or learn from the expense."
            )
            decision_unlocked = "Clearer purchase classification, stock attribution, and overhead tracking"

        gaps.append(
            {
                "agenda_type": "purchase_explanation",
                "priority": priority,
                "title": title,
                "question": question,
                "why_it_matters": why_it_matters,
                "decision_unlocked": decision_unlocked,
                "reason_codes": reason_codes,
                "evidence": {
                    "supplier": group["supplier"],
                    "line_description": group["line_description"],
                    "occurrences": group["occurrences"],
                    "line_total_cents": group["line_total_cents"],
                    "latest_bill_date": group["latest_bill_date"],
                    "suggested_score_key": group.get("suggested_score_key"),
                    "review_ids": group["review_ids"],
                },
            }
        )

    return gaps


def detect_wage_explanation_gaps(
    site_id: str,
    *,
    daily_profitability: list[dict] | None = None,
    bottom_line_scorecard: dict | None = None,
    lookback_days: int = 14,
    limit: int = 1,
) -> list[dict]:
    if bottom_line_scorecard is None:
        try:
            bottom_line_scorecard = get_bottom_line_scorecard(site_id, days=30, compare_days=7)
        except Exception:
            bottom_line_scorecard = {}

    if daily_profitability is None:
        try:
            end_date = date.today()
            start_date = end_date - timedelta(days=max(1, int(lookback_days or 14)) - 1)
            daily_profitability = get_daily_profitability(site_id, start_date, end_date)
        except Exception:
            daily_profitability = []

    targets = (bottom_line_scorecard or {}).get("targets") or {}
    target_config = targets.get("targets") or {}
    gaps = targets.get("gaps") or {}
    financial_truth = (bottom_line_scorecard or {}).get("financial_truth") or {}

    labor_gap_cents = int(gaps.get("weekly_labor_reduction_needed_cents") or 0)
    labor_pct_high = float(target_config.get("labor_pct_high") or LABOR_PCT_TARGET_HIGH)
    labor_source = str(financial_truth.get("labor_truth_source") or "operational_labor_proxy")
    source_label = "Wage %" if labor_source == "xero_payroll" else "Labor %"

    usable_days = [
        row
        for row in (daily_profitability or [])
        if row.get("labor_pct") is not None and int(row.get("revenue_cents") or 0) > 0
    ]
    if not usable_days or labor_gap_cents <= 0:
        return []

    avg_labor_pct = sum(float(row["labor_pct"]) for row in usable_days) / len(usable_days)
    worst_day = max(
        usable_days,
        key=lambda row: (
            float(row.get("labor_pct") or 0) - labor_pct_high,
            int(row.get("labor_cost_cents") or 0),
            -int(row.get("revenue_cents") or 0),
        ),
    )
    worst_labor_pct = float(worst_day.get("labor_pct") or 0)
    if worst_labor_pct < max(labor_pct_high + 2.0, avg_labor_pct + 1.5):
        return []

    priority = "high" if worst_labor_pct >= labor_pct_high + 5.0 else "medium"
    revenue_cents = int(worst_day.get("revenue_cents") or 0)
    labor_cost_cents = int(worst_day.get("labor_cost_cents") or 0)
    issue_hint = ""
    labor_issues = worst_day.get("labor_data_issues")
    if labor_issues:
        issue_hint = f" Reported data issues: {labor_issues}."

    return [
        {
            "agenda_type": "labor_explanation",
            "priority": priority,
            "title": f"Explain recent {source_label.lower()} spike",
            "question": (
                f"{source_label} hit {worst_labor_pct:.1f}% on {worst_day.get('date')}. "
                "Was that training, sick cover, public-holiday rates, unusually soft trade, or an intentional roster choice?"
            ),
            "why_it_matters": (
                f"{source_label} is still above target by {_format_cents_short(labor_gap_cents)}. "
                f"The biggest recent spike was {worst_labor_pct:.1f}% on {worst_day.get('date')} "
                f"against ${revenue_cents / 100:,.0f} revenue and ${labor_cost_cents / 100:,.0f} labor.{issue_hint}"
            ),
            "decision_unlocked": (
                "Separate intentional labor investment from avoidable inefficiency in roster recommendations"
            ),
            "evidence": {
                "date": worst_day.get("date"),
                "labor_pct": worst_labor_pct,
                "avg_labor_pct": round(avg_labor_pct, 2),
                "labor_pct_high": labor_pct_high,
                "revenue_cents": revenue_cents,
                "labor_cost_cents": labor_cost_cents,
                "labor_gap_cents": labor_gap_cents,
                "labor_truth_source": labor_source,
            },
        }
    ][: max(1, int(limit or 1))]
