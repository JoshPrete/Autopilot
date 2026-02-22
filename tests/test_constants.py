"""
Tests for config/constants.py - Policy Validation

Ensures all immutable policy values from the spec are correct
and internally consistent.
"""

import pytest

from config.constants import (
    BASE_DRINK_SCORES,
    BASE_FOOD_SCORES,
    BASE_RETAIL_SCORES,
    DOW_PATTERN_DEFAULT,
    MILK_BUFFER_MULTIPLIER,
    MILK_DRINK_RATIO,
    MILK_PER_DRINK_ML,
    MILK_SPLIT_DEFAULT,
    MODIFIER_ADJUSTMENTS,
    POSITION_MULTIPLIERS,
    PRIORITY_STACK,
    ROLES,
    STATES,
    TRANSITION_2P_TO_3P,
    TRANSITION_DELIVERY_OVERRIDE,
    TRANSITION_P1_TO_SHOTS,
    TRANSITION_RUSH_END,
    TRANSITION_WALLY_ACTIVATE,
    WALLY_MODES,
)


class TestPriorityStack:
    def test_four_levels(self):
        assert len(PRIORITY_STACK) == 4

    def test_priority_order(self):
        """Priority 1 = customers, 4 = prep."""
        assert "customer" in PRIORITY_STACK[1].lower() or "serve" in PRIORITY_STACK[1].lower()
        assert "prep" in PRIORITY_STACK[4].lower()


class TestStates:
    def test_five_states(self):
        assert len(STATES) == 5

    def test_all_states_have_staff(self):
        for state, info in STATES.items():
            assert "staff" in info
            assert info["staff"] >= 1

    def test_staff_counts(self):
        assert STATES["S1P_SURVIVAL"]["staff"] == 1
        assert STATES["S2P_STANDARD"]["staff"] == 2
        assert STATES["S2P_BUSY"]["staff"] == 2
        assert STATES["S3P_RUSH"]["staff"] == 3
        assert STATES["S4P_STANDARD"]["staff"] == 4


class TestBaseDrinkScores:
    def test_all_drinks_present(self):
        expected = [
            "espresso",
            "long_black",
            "latte",
            "cappuccino",
            "flat_white",
            "mocha",
            "iced_latte",
            "matcha_complex",
            "babycino",
        ]
        for drink in expected:
            assert drink in BASE_DRINK_SCORES

    def test_scores_have_units_and_time(self):
        for drink, info in BASE_DRINK_SCORES.items():
            assert "units" in info
            assert "avg_time_sec" in info
            assert info["units"] > 0
            assert info["avg_time_sec"] > 0

    def test_spec_values(self):
        """Verify exact values from Spec Section 5.1."""
        assert BASE_DRINK_SCORES["espresso"]["units"] == 1.0
        assert BASE_DRINK_SCORES["latte"]["units"] == 2.5
        assert BASE_DRINK_SCORES["flat_white"]["units"] == 2.8
        assert BASE_DRINK_SCORES["mocha"]["units"] == 3.5
        assert BASE_DRINK_SCORES["matcha_complex"]["units"] == 4.0
        assert BASE_DRINK_SCORES["babycino"]["units"] == 0.5

    def test_complexity_ordering(self):
        """Espresso < latte < mocha < matcha."""
        assert BASE_DRINK_SCORES["espresso"]["units"] < BASE_DRINK_SCORES["latte"]["units"]
        assert BASE_DRINK_SCORES["latte"]["units"] < BASE_DRINK_SCORES["mocha"]["units"]
        assert BASE_DRINK_SCORES["mocha"]["units"] < BASE_DRINK_SCORES["matcha_complex"]["units"]


class TestBaseFoodScores:
    def test_all_food_present(self):
        expected = ["toastie", "wrap", "croissant", "muffin", "pastry", "cookie", "tart"]
        for item in expected:
            assert item in BASE_FOOD_SCORES

    def test_scores_have_units_and_time(self):
        for item, info in BASE_FOOD_SCORES.items():
            assert "units" in info
            assert "avg_time_sec" in info
            assert info["units"] > 0
            assert info["avg_time_sec"] > 0

    def test_hot_food_higher_than_grab(self):
        """Toasties/wraps should score higher than grab-and-go items."""
        assert BASE_FOOD_SCORES["toastie"]["units"] > BASE_FOOD_SCORES["croissant"]["units"]
        assert BASE_FOOD_SCORES["wrap"]["units"] > BASE_FOOD_SCORES["muffin"]["units"]


class TestBaseRetailScores:
    def test_all_retail_present(self):
        expected = ["water", "juice", "soda", "kombucha", "beans", "gift_card", "merchandise"]
        for item in expected:
            assert item in BASE_RETAIL_SCORES

    def test_scores_have_units_and_time(self):
        for item, info in BASE_RETAIL_SCORES.items():
            assert "units" in info
            assert "avg_time_sec" in info
            assert info["units"] > 0
            assert info["avg_time_sec"] > 0

    def test_retail_lower_than_drinks(self):
        """All retail items should score lower than any drink."""
        max_retail = max(info["units"] for info in BASE_RETAIL_SCORES.values())
        min_drink = min(info["units"] for info in BASE_DRINK_SCORES.values())
        assert max_retail < min_drink


class TestModifiers:
    def test_all_modifiers_present(self):
        expected = ["alt_milk", "extra_shot", "decaf", "syrup", "iced", "large"]
        for mod in expected:
            assert mod in MODIFIER_ADJUSTMENTS

    def test_spec_values(self):
        """Verify exact values from Spec Section 5.2."""
        assert MODIFIER_ADJUSTMENTS["alt_milk"]["units_add"] == 0.5
        assert MODIFIER_ADJUSTMENTS["extra_shot"]["units_add"] == 0.5
        assert MODIFIER_ADJUSTMENTS["decaf"]["units_add"] == 0.3
        assert MODIFIER_ADJUSTMENTS["syrup"]["units_add"] == 0.4


class TestPositionMultipliers:
    def test_spec_values(self):
        """Verify exact values from Spec Section 5.3."""
        assert POSITION_MULTIPLIERS[1] == 1.0
        assert POSITION_MULTIPLIERS[2] == 1.3
        assert POSITION_MULTIPLIERS[3] == 1.6


class TestTransitionThresholds:
    def test_2p_to_3p(self):
        assert TRANSITION_2P_TO_3P["workload_multiplier"] == 1.5
        assert TRANSITION_2P_TO_3P["orders_per_5min"] == 6
        assert TRANSITION_2P_TO_3P["drinks_in_progress"] == 8
        assert TRANSITION_2P_TO_3P["lead_time_minutes"] == 7

    def test_wally_activate(self):
        assert TRANSITION_WALLY_ACTIVATE["milk_drinks_queued"] == 3
        assert TRANSITION_WALLY_ACTIVATE["workload_multiplier"] == 1.3

    def test_rush_end(self):
        assert TRANSITION_RUSH_END["below_baseline_minutes"] == 10


class TestDowPattern:
    def test_seven_days(self):
        assert len(DOW_PATTERN_DEFAULT) == 7

    def test_spec_values(self):
        assert DOW_PATTERN_DEFAULT["Monday"] == 0.85
        assert DOW_PATTERN_DEFAULT["Friday"] == 1.12
        assert DOW_PATTERN_DEFAULT["Saturday"] == 1.15

    def test_average_near_one(self):
        """Day-of-week factors should average close to 1.0."""
        avg = sum(DOW_PATTERN_DEFAULT.values()) / 7
        assert 0.95 <= avg <= 1.05


class TestMilkConstants:
    def test_spec_values(self):
        assert MILK_PER_DRINK_ML == 180
        assert MILK_BUFFER_MULTIPLIER == 1.2
        assert MILK_DRINK_RATIO == 0.70

    def test_split_sums_to_one(self):
        total = sum(MILK_SPLIT_DEFAULT.values())
        assert total == pytest.approx(1.0)

    def test_split_values(self):
        assert MILK_SPLIT_DEFAULT["full_cream"] == 0.60
        assert MILK_SPLIT_DEFAULT["oat"] == 0.30
        assert MILK_SPLIT_DEFAULT["soy"] == 0.10


class TestRoles:
    def test_four_roles(self):
        assert len(ROLES) == 4
        assert "P1" in ROLES
        assert "P2" in ROLES
        assert "P3" in ROLES
        assert "MANAGER" in ROLES

    def test_roles_have_responsibilities(self):
        for role, info in ROLES.items():
            assert "primary" in info
            assert "secondary" in info

    def test_wally_mode_count(self):
        assert len(WALLY_MODES) == 3
        assert "OFF" in WALLY_MODES
        assert "ON_DEMAND" in WALLY_MODES
        assert "CYCLING" in WALLY_MODES
