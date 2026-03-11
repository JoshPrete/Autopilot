"""
Tests for operator_knowledge structured parsers.
Covers: purchase_profile, workflow_rule, and family-level recipe_definition.
"""

import pytest

from app.operator_knowledge import (
    parse_operator_rule_message,
    summarize_operator_rule,
)


# ---------------------------------------------------------------------------
# purchase_profile
# ---------------------------------------------------------------------------


def test_purchase_profile_with_supplier_and_pack():
    result = parse_operator_rule_message(
        "We buy oat milk from Dairyco in 12-pack cartons"
    )
    assert result is not None
    assert result["rule_type"] == "purchase_profile"
    p = result["payload"]
    assert p["subject"] == "oat milk"
    assert p["supplier_name"] == "Dairyco"
    assert p["pack_size"] == 12
    assert p["pack_unit"] == "cartons"


def test_purchase_profile_comes_in_with_supplier():
    result = parse_operator_rule_message(
        "Full cream milk comes in 2L bottles from Pauls Dairy"
    )
    assert result is not None
    assert result["rule_type"] == "purchase_profile"
    p = result["payload"]
    assert p["subject"].lower() == "full cream milk"
    assert p["pack_size"] == 2
    assert p["pack_unit"] == "l"
    assert p["supplier_name"] == "Pauls Dairy"


def test_purchase_profile_comes_in_no_supplier():
    result = parse_operator_rule_message(
        "Oat milk comes in 10L bag-in-box"
    )
    assert result is not None
    assert result["rule_type"] == "purchase_profile"
    p = result["payload"]
    assert p["subject"].lower() == "oat milk"
    assert p["pack_size"] == 10
    assert p["supplier_name"] is None


def test_purchase_profile_minimum_order():
    result = parse_operator_rule_message(
        "Minimum order for full cream milk is 3 cases"
    )
    assert result is not None
    assert result["rule_type"] == "purchase_profile"
    p = result["payload"]
    assert p["subject"] == "full cream milk"
    assert p["pack_size"] == 3
    assert p["pack_unit"] == "cases"


def test_purchase_profile_summary_with_supplier():
    rule = {
        "rule_type": "purchase_profile",
        "payload": {
            "subject": "oat milk",
            "supplier_name": "Dairyco",
            "pack_size": 12,
            "pack_unit": "cartons",
        },
    }
    assert summarize_operator_rule(rule) == "oat milk: 12 cartons from Dairyco"


def test_purchase_profile_summary_no_supplier():
    rule = {
        "rule_type": "purchase_profile",
        "payload": {
            "subject": "oat milk",
            "supplier_name": None,
            "pack_size": 10,
            "pack_unit": "l",
        },
    }
    assert summarize_operator_rule(rule) == "oat milk: ordered in 10 l"


# ---------------------------------------------------------------------------
# workflow_rule
# ---------------------------------------------------------------------------


def test_workflow_rule_handoff():
    result = parse_operator_rule_message(
        "Barista hands off to runner when queue hits 4 drinks"
    )
    assert result is not None
    assert result["rule_type"] == "workflow_rule"
    p = result["payload"]
    assert p["role_source"] == "barista"
    assert p["role_target"] == "runner"
    assert "4" in p["trigger_condition"]
    assert "handoff" in p["action"]


def test_workflow_rule_call_backup():
    result = parse_operator_rule_message(
        "Call backup bar when orders reach 6"
    )
    assert result is not None
    assert result["rule_type"] == "workflow_rule"
    p = result["payload"]
    assert p["trigger_condition"] == "queue >= 6"
    assert "backup bar" in p["action"]
    assert p["role_source"] is None


def test_workflow_rule_prep_timing():
    result = parse_operator_rule_message(
        "Cold brew is prepped the night before"
    )
    assert result is not None
    assert result["rule_type"] == "workflow_rule"
    p = result["payload"]
    assert p["subject"] == "cold brew"
    assert "night before" in p["trigger_condition"]
    assert "cold brew" in p["action"]


def test_workflow_rule_prep_at_close():
    result = parse_operator_rule_message(
        "Batch brew is prepped during close"
    )
    assert result is not None
    assert result["rule_type"] == "workflow_rule"
    p = result["payload"]
    assert p["subject"] == "batch brew"
    assert "close" in p["trigger_condition"]


def test_workflow_rule_summary_handoff():
    rule = {
        "rule_type": "workflow_rule",
        "payload": {
            "trigger_condition": "queue >= 4",
            "action": "handoff to runner",
            "role_source": "barista",
            "role_target": "runner",
            "subject": None,
            "timing": None,
        },
    }
    summary = summarize_operator_rule(rule)
    assert "barista" in summary
    assert "handoff to runner" in summary
    assert "queue >= 4" in summary


def test_workflow_rule_summary_prep():
    rule = {
        "rule_type": "workflow_rule",
        "payload": {
            "trigger_condition": "prep_timing: the night before",
            "action": "prep cold brew",
            "role_source": None,
            "role_target": None,
            "subject": "cold brew",
            "timing": "the night before",
        },
    }
    summary = summarize_operator_rule(rule)
    assert "cold brew" in summary


# ---------------------------------------------------------------------------
# family-level recipe_definition ("all X use Y")
# ---------------------------------------------------------------------------


def test_family_recipe_all_iced_lattes():
    result = parse_operator_rule_message("All iced lattes use 60g ice")
    assert result is not None
    assert result["rule_type"] == "recipe_definition"
    p = result["payload"]
    assert p["is_family_rule"] is True
    assert p["trigger_item_name"] == "iced latte"
    assert p["sale_profile"]["family"] == "latte"
    assert p["sale_profile"]["serve_temperature"] == "iced"
    assert any(c["item_name"] == "ice" for c in p["components"])


def test_family_recipe_all_takeaway_drinks():
    result = parse_operator_rule_message("All takeaway drinks use 1 lid and 1 sleeve")
    assert result is not None
    assert result["rule_type"] == "recipe_definition"
    p = result["payload"]
    assert p["is_family_rule"] is True
    assert "takeaway" in p["trigger_item_name"]
    assert len(p["components"]) == 2


def test_family_recipe_all_large_flat_whites():
    result = parse_operator_rule_message("All large flat whites use 280ml milk")
    assert result is not None
    assert result["rule_type"] == "recipe_definition"
    p = result["payload"]
    assert p["is_family_rule"] is True
    assert p["sale_profile"]["family"] == "flat_white"
    assert p["sale_profile"]["size_label"] == "large"
    assert p["components"][0]["quantity"] == 280.0


def test_family_recipe_summary():
    rule = {
        "rule_type": "recipe_definition",
        "payload": {
            "trigger_item_name": "iced latte",
            "sale_profile": {"family": "latte", "serve_temperature": "iced"},
            "components": [{"item_name": "ice", "quantity": 60.0, "unit": "g"}],
            "is_family_rule": True,
        },
    }
    summary = summarize_operator_rule(rule)
    assert "iced latte" in summary
    assert "60 g ice" in summary


# ---------------------------------------------------------------------------
# Regression: existing parsers still work
# ---------------------------------------------------------------------------


def test_existing_delivery_schedule_unaffected():
    result = parse_operator_rule_message(
        "Milk delivery is Monday, Wednesday, Friday"
    )
    assert result is not None
    assert result["rule_type"] == "delivery_schedule"


def test_existing_recipe_definition_unaffected():
    result = parse_operator_rule_message(
        "12oz latte uses 20g beans and 280ml full cream milk"
    )
    assert result is not None
    assert result["rule_type"] == "recipe_definition"
    assert result["payload"].get("is_family_rule") is None
    assert result["payload"]["trigger_item_name"] == "12oz latte"
