"""
Tests for item_cost rule capture in app/operator_knowledge.py
and missing_item_cost gap detection in analysis/knowledge_gaps.py
"""

import pytest
from unittest.mock import patch

from app.operator_knowledge import (
    parse_operator_rule_message,
    summarize_operator_rule,
)


# ---------------------------------------------------------------------------
# Parser: _parse_item_cost
# ---------------------------------------------------------------------------

def _parse(msg):
    return parse_operator_rule_message(msg)


def test_cost_basic():
    r = _parse("flat white costs $1.80")
    assert r is not None
    assert r["rule_type"] == "item_cost"
    assert r["payload"]["item_name"] == "flat white"
    assert r["payload"]["cost_cents"] == 180


def test_cost_with_us():
    r = _parse("flat white costs us $1.80")
    assert r is not None
    assert r["payload"]["cost_cents"] == 180


def test_cost_with_to_make():
    r = _parse("flat white costs $1.80 to make")
    assert r is not None
    assert r["payload"]["cost_cents"] == 180


def test_cost_making_prefix():
    r = _parse("making a flat white costs $1.80")
    assert r is not None
    assert r["payload"]["item_name"] == "flat white"
    assert r["payload"]["cost_cents"] == 180


def test_cost_the_cost_of():
    r = _parse("the cost of a flat white is $2.00")
    assert r is not None
    assert r["payload"]["item_name"] == "flat white"
    assert r["payload"]["cost_cents"] == 200


def test_cost_cogs_for():
    r = _parse("COGS for flat white is $1.80")
    assert r is not None
    assert r["payload"]["cost_cents"] == 180


def test_cost_our_x_cogs_is():
    r = _parse("our flat white costs is $1.80")
    assert r is not None
    assert r["payload"]["cost_cents"] == 180


def test_cost_decimal_two_places():
    r = _parse("cappuccino costs $2.25 to make")
    assert r is not None
    assert r["payload"]["cost_cents"] == 225


def test_cost_about():
    r = _parse("a 12oz latte costs about $1.50")
    assert r is not None
    assert r["payload"]["item_name"] == "12oz latte"
    assert r["payload"]["cost_cents"] == 150


def test_cost_zero_not_captured():
    """A cost of $0 is not a valid capture."""
    r = _parse("flat white costs $0")
    assert r is None


def test_cost_question_not_captured():
    r = _parse("what does a flat white cost?")
    assert r is None


def test_cost_no_price_not_captured():
    r = _parse("flat white costs a lot")
    assert r is None


# ---------------------------------------------------------------------------
# summarize_operator_rule for item_cost
# ---------------------------------------------------------------------------

def test_summarize_item_cost():
    rule = {"rule_type": "item_cost", "payload": {"item_name": "flat white", "cost_cents": 180}}
    summary = summarize_operator_rule(rule)
    assert "flat white" in summary
    assert "$1.80" in summary


def test_summarize_item_cost_no_cost():
    rule = {"rule_type": "item_cost", "payload": {"item_name": "latte", "cost_cents": None}}
    summary = summarize_operator_rule(rule)
    assert "latte" in summary


# ---------------------------------------------------------------------------
# missing_item_cost gap detection
# ---------------------------------------------------------------------------

from analysis.knowledge_gaps import detect_knowledge_gaps


_TOP_ITEMS = [
    {"item": "Flat White", "count": 50, "avg_workload": 1.0},
    {"item": "Batch Brew", "count": 30, "avg_workload": 0.5},
]

_COST_RECORDS_DEFAULT = [
    {"score_key": "flat_white", "source": "default"},
    {"score_key": "batch_brew", "source": "default"},
]

_COST_RECORDS_CONFIRMED = [
    {"score_key": "flat_white", "source": "operator"},
    {"score_key": "batch_brew", "source": "default"},
]


def _patch_costs(records):
    return patch("analysis.knowledge_gaps.get_item_costs_detailed", return_value=records)


def _patch_resolve(mapping):
    """mapping: {item_name_lower: score_key}"""
    def _resolve(name):
        key = name.lower().replace(" ", "_")
        return mapping.get(key, key), "drink"
    return patch("data.processing.resolve_item_key", side_effect=_resolve)


def test_missing_item_cost_gap_detected():
    with _patch_costs(_COST_RECORDS_DEFAULT), \
         _patch("analysis.knowledge_gaps.get_item_costs_detailed", return_value=_COST_RECORDS_DEFAULT), \
         patch("analysis.knowledge_gaps.get_inventory_alerts", return_value=[]), \
         patch("analysis.knowledge_gaps.list_inventory_usage_rules", return_value=[]), \
         patch("analysis.knowledge_gaps.list_operator_rules", return_value=[]), \
         patch("analysis.knowledge_gaps._get_top_items", return_value=_TOP_ITEMS):
        gaps = detect_knowledge_gaps("site_1")
    gap_types = [g["gap_type"] for g in gaps]
    assert "missing_item_cost" in gap_types


def test_confirmed_cost_suppresses_gap():
    """Items with source != 'default' should NOT produce a missing_item_cost gap."""
    with _patch_costs(_COST_RECORDS_CONFIRMED), \
         patch("analysis.knowledge_gaps.get_item_costs_detailed", return_value=_COST_RECORDS_CONFIRMED), \
         patch("analysis.knowledge_gaps.get_inventory_alerts", return_value=[]), \
         patch("analysis.knowledge_gaps.list_inventory_usage_rules", return_value=[]), \
         patch("analysis.knowledge_gaps.list_operator_rules", return_value=[]), \
         patch("analysis.knowledge_gaps._get_top_items", return_value=_TOP_ITEMS):
        gaps = detect_knowledge_gaps("site_1")
    # flat_white has source=operator so should not appear
    cost_gaps = [g for g in gaps if g["gap_type"] == "missing_item_cost"]
    item_names = [g["evidence"]["item_name"] for g in cost_gaps]
    assert "Flat White" not in item_names


def test_low_volume_item_not_surfaced():
    """Items below threshold (20 sales) should not generate a cost gap."""
    low_items = [{"item": "Flat White", "count": 5, "avg_workload": 1.0}]
    with _patch_costs(_COST_RECORDS_DEFAULT), \
         patch("analysis.knowledge_gaps.get_item_costs_detailed", return_value=_COST_RECORDS_DEFAULT), \
         patch("analysis.knowledge_gaps.get_inventory_alerts", return_value=[]), \
         patch("analysis.knowledge_gaps.list_inventory_usage_rules", return_value=[]), \
         patch("analysis.knowledge_gaps.list_operator_rules", return_value=[]), \
         patch("analysis.knowledge_gaps._get_top_items", return_value=low_items):
        gaps = detect_knowledge_gaps("site_1")
    cost_gaps = [g for g in gaps if g["gap_type"] == "missing_item_cost"]
    assert len(cost_gaps) == 0


def _patch(target, **kwargs):
    return patch(target, **kwargs)
