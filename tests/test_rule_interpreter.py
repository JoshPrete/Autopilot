"""
Tests for decisions/rule_interpreter.py

Covers: purchase_profile enrichment of ordering actions, workflow_rule
handling, and regression checks for existing rule types.
"""

from datetime import date

import pytest

from decisions.rule_interpreter import apply_rules


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _rule(rule_type: str, payload: dict) -> dict:
    return {"rule_type": rule_type, "payload": payload}


# Monday = 0 in Python's weekday()
MONDAY = date(2026, 3, 9)    # Monday
TUESDAY = date(2026, 3, 10)  # Tuesday
WEDNESDAY = date(2026, 3, 11)  # Wednesday
FRIDAY = date(2026, 3, 13)   # Friday
SATURDAY = date(2026, 3, 14) # Saturday


# ---------------------------------------------------------------------------
# purchase_profile — enriches ordering_schedule
# ---------------------------------------------------------------------------

def test_ordering_enriched_with_purchase_profile_supplier_and_pack():
    rules = [
        _rule("ordering_schedule", {
            "subject": "oat milk",
            "cutoff_day": "tuesday",
            "cutoff_time": "14:00",
            "delivery_day": "wednesday",
        }),
        _rule("purchase_profile", {
            "subject": "oat milk",
            "supplier_name": "Dairyco",
            "pack_size": 12,
            "pack_unit": "cartons",
        }),
    ]
    actions = apply_rules(rules, TUESDAY)
    assert len(actions) == 1
    assert "oat milk" in actions[0]
    assert "Dairyco" in actions[0]
    assert "12 × cartons" in actions[0]
    assert "Wednesday" in actions[0]


def test_ordering_enriched_with_pack_only_no_supplier():
    rules = [
        _rule("ordering_schedule", {
            "subject": "full cream milk",
            "cutoff_day": "monday",
            "cutoff_time": "09:00",
            "delivery_day": "tuesday",
        }),
        _rule("purchase_profile", {
            "subject": "full cream milk",
            "supplier_name": None,
            "pack_size": 6,
            "pack_unit": "bottles",
        }),
    ]
    actions = apply_rules(rules, MONDAY)
    assert len(actions) == 1
    assert "6 × bottles" in actions[0]
    assert "from" not in actions[0]


def test_ordering_enriched_with_supplier_only_no_pack():
    rules = [
        _rule("ordering_schedule", {
            "subject": "beans",
            "cutoff_day": "wednesday",
            "cutoff_time": "12:00",
            "delivery_day": "friday",
        }),
        _rule("purchase_profile", {
            "subject": "beans",
            "supplier_name": "Single O",
            "pack_size": None,
            "pack_unit": "kg",
        }),
    ]
    actions = apply_rules(rules, WEDNESDAY)
    assert len(actions) == 1
    assert "from Single O" in actions[0]


def test_ordering_without_matching_purchase_profile_unchanged():
    rules = [
        _rule("ordering_schedule", {
            "subject": "oat milk",
            "cutoff_day": "tuesday",
            "cutoff_time": "14:00",
            "delivery_day": "wednesday",
        }),
    ]
    actions = apply_rules(rules, TUESDAY)
    assert len(actions) == 1
    assert "oat milk" in actions[0]
    assert "×" not in actions[0]
    assert "from" not in actions[0]


def test_purchase_profile_alone_produces_no_action():
    """purchase_profile is not standalone-actionable."""
    rules = [
        _rule("purchase_profile", {
            "subject": "oat milk",
            "supplier_name": "Dairyco",
            "pack_size": 12,
            "pack_unit": "cartons",
        }),
    ]
    actions = apply_rules(rules, TUESDAY)
    assert actions == []


def test_purchase_profile_subject_match_is_case_insensitive():
    rules = [
        _rule("ordering_schedule", {
            "subject": "Oat Milk",
            "cutoff_day": "tuesday",
            "cutoff_time": "14:00",
            "delivery_day": "wednesday",
        }),
        _rule("purchase_profile", {
            "subject": "oat milk",
            "supplier_name": "Dairyco",
            "pack_size": 12,
            "pack_unit": "cartons",
        }),
    ]
    actions = apply_rules(rules, TUESDAY)
    assert "Dairyco" in actions[0]


# ---------------------------------------------------------------------------
# workflow_rule — prep timing
# ---------------------------------------------------------------------------

def test_workflow_night_before_fires_every_day():
    rules = [
        _rule("workflow_rule", {
            "trigger_condition": "prep_timing: the night before",
            "action": "prep cold brew",
            "role_source": None,
            "role_target": None,
            "subject": "cold brew",
            "timing": "the night before",
        }),
    ]
    for forecast_date in (MONDAY, TUESDAY, WEDNESDAY, FRIDAY, SATURDAY):
        actions = apply_rules(rules, forecast_date)
        assert len(actions) == 1
        assert "Before close" in actions[0]
        assert "cold brew" in actions[0]


def test_workflow_close_timing():
    rules = [
        _rule("workflow_rule", {
            "trigger_condition": "prep_timing: during close",
            "action": "prep batch brew",
            "role_source": None,
            "role_target": None,
            "subject": "batch brew",
            "timing": "during close",
        }),
    ]
    actions = apply_rules(rules, TUESDAY)
    assert "Before close" in actions[0]
    assert "batch brew" in actions[0]


def test_workflow_open_timing():
    rules = [
        _rule("workflow_rule", {
            "trigger_condition": "prep_timing: before open",
            "action": "prep filter coffee",
            "role_source": None,
            "role_target": None,
            "subject": "filter coffee",
            "timing": "before open",
        }),
    ]
    actions = apply_rules(rules, MONDAY)
    assert "At open" in actions[0]
    assert "filter coffee" in actions[0]


# ---------------------------------------------------------------------------
# workflow_rule — handoff / threshold
# ---------------------------------------------------------------------------

def test_workflow_handoff_fires_every_day():
    rules = [
        _rule("workflow_rule", {
            "trigger_condition": "queue >= 4",
            "action": "handoff to runner",
            "role_source": "barista",
            "role_target": "runner",
            "subject": None,
            "timing": None,
        }),
    ]
    actions = apply_rules(rules, WEDNESDAY)
    assert len(actions) == 1
    assert "barista" in actions[0]
    assert "queue >= 4" in actions[0]
    assert "handoff to runner" in actions[0]


def test_workflow_threshold_call_backup():
    rules = [
        _rule("workflow_rule", {
            "trigger_condition": "queue >= 6",
            "action": "call backup bar",
            "role_source": None,
            "role_target": "backup bar",
            "subject": None,
            "timing": None,
        }),
    ]
    actions = apply_rules(rules, FRIDAY)
    assert "queue >= 6" in actions[0]
    assert "call backup bar" in actions[0]


def test_workflow_rule_with_no_action_produces_nothing():
    rules = [
        _rule("workflow_rule", {
            "trigger_condition": "queue >= 4",
            "action": "",
            "role_source": None,
            "role_target": None,
            "subject": None,
            "timing": None,
        }),
    ]
    assert apply_rules(rules, MONDAY) == []


# ---------------------------------------------------------------------------
# Regression — existing rule types unaffected
# ---------------------------------------------------------------------------

def test_delivery_schedule_regression():
    rules = [_rule("delivery_schedule", {"subject": "Milk", "days": ["tuesday", "friday"]})]
    assert apply_rules(rules, MONDAY) == []
    actions = apply_rules(rules, TUESDAY)
    assert "Milk delivery today" in actions[0]


def test_staffing_constraint_regression():
    rules = [_rule("staffing_constraint", {
        "day_of_week": "saturday",
        "daypart": "open",
        "min_staff": 3,
        "requires_senior": True,
        "disallow_role_alone": None,
    })]
    assert apply_rules(rules, MONDAY) == []
    actions = apply_rules(rules, SATURDAY)
    assert "minimum 3 staff" in actions[0]
    assert "senior" in actions[0]


def test_storage_rule_regression():
    rules = [_rule("storage_rule", {
        "subject": "Cold brew",
        "storage_location": "cool room",
        "condition": None,
    })]
    actions = apply_rules(rules, WEDNESDAY)
    assert "Cold brew" in actions[0]
    assert "cool room" in actions[0]


def test_recipe_definition_produces_no_action():
    rules = [_rule("recipe_definition", {
        "trigger_item_name": "12oz latte",
        "components": [{"item_name": "milk", "quantity": 280, "unit": "ml"}],
    })]
    assert apply_rules(rules, MONDAY) == []


def test_combined_rules_ordering_and_workflow_both_fire():
    rules = [
        _rule("ordering_schedule", {
            "subject": "oat milk",
            "cutoff_day": "tuesday",
            "cutoff_time": "14:00",
            "delivery_day": "wednesday",
        }),
        _rule("purchase_profile", {
            "subject": "oat milk",
            "supplier_name": "Dairyco",
            "pack_size": 10,
            "pack_unit": "l",
        }),
        _rule("workflow_rule", {
            "trigger_condition": "prep_timing: the night before",
            "action": "prep cold brew",
            "role_source": None,
            "role_target": None,
            "subject": "cold brew",
            "timing": "the night before",
        }),
    ]
    actions = apply_rules(rules, TUESDAY)
    assert len(actions) == 2
    order_action = next(a for a in actions if "oat milk" in a)
    prep_action = next(a for a in actions if "cold brew" in a)
    assert "Dairyco" in order_action
    assert "Before close" in prep_action
