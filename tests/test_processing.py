"""
Tests for data/processing.py - Workload Calculation Engine

Covers Spec Sections 5.1-5.5:
- Base drink scores
- Modifier adjustments
- Multi-drink position penalties
- The exact example from Section 5.4 (11.38 units)
- Timeline aggregation
- Wally volume calculation
- 4-layer composite prediction
"""

import pytest

from data.processing import (
    ITEM_CATALOG,
    MODIFIER_NAME_MAP,
    aggregate_timeline,
    calculate_item_workload,
    calculate_order_workload,
    calculate_wally_volume,
    predict_workload,
    process_orders_batch,
    resolve_drink_key,
    resolve_item_key,
    resolve_modifier_key,
)


# ============================================================
# Drink Name Resolution
# ============================================================


class TestResolveItemKey:
    """Test the new resolve_item_key that returns (score_key, category)."""

    def test_square_hot_drink_sizes(self):
        assert resolve_item_key("12oz/Medium") == ("latte", "drink")
        assert resolve_item_key("8oz/Small") == ("flat_white", "drink")
        assert resolve_item_key("16oz/Large") == ("latte", "drink")
        assert resolve_item_key("6oz") == ("espresso", "drink")

    def test_square_iced_drink_sizes(self):
        assert resolve_item_key("16oz/ICED MEDIUM") == ("iced_latte", "drink")
        assert resolve_item_key("12oz/ICED SMALL") == ("iced_latte", "drink")
        assert resolve_item_key("20oz\\ICED LARGE") == ("iced_latte", "drink")

    def test_espresso_shots(self):
        assert resolve_item_key("4oz/Pic, Esp, Dop") == ("espresso", "drink")

    def test_babycino_pupacino(self):
        assert resolve_item_key("Babycino") == ("babycino", "drink")
        assert resolve_item_key("Pupacino") == ("babycino", "drink")

    def test_food_items(self):
        assert resolve_item_key("TOASTIE") == ("toastie", "food")
        assert resolve_item_key("BUTTERBOY") == ("butterboy", "food")
        assert resolve_item_key("Breakfast Wrap") == ("wrap", "food")
        assert resolve_item_key("Ham And Cheese Croissant") == ("croissant", "food")
        assert resolve_item_key("A Sweet Muffin") == ("muffin", "food")
        assert resolve_item_key("Sweet Pastry") == ("pastry", "food")
        assert resolve_item_key("A Cookie") == ("cookie", "food")

    def test_retail_items(self):
        assert resolve_item_key("Fiji Water") == ("water", "retail")
        assert resolve_item_key("Fruit Juice") == ("juice", "retail")
        assert resolve_item_key("Kombucha") == ("kombucha", "retail")
        assert resolve_item_key("500g Beans") == ("beans", "retail")
        assert resolve_item_key("eGift Card") == ("gift_card", "retail")
        assert resolve_item_key("Candle") == ("merchandise", "retail")

    def test_legacy_drink_names(self):
        assert resolve_item_key("latte") == ("latte", "drink")
        assert resolve_item_key("cappuccino") == ("cappuccino", "drink")
        assert resolve_item_key("flat white") == ("flat_white", "drink")
        assert resolve_item_key("mocha") == ("mocha", "drink")

    def test_unknown_defaults_to_latte(self):
        assert resolve_item_key("Mystery Drink XYZ") == ("latte", "drink")

    def test_case_insensitive(self):
        assert resolve_item_key("TOASTIE") == ("toastie", "food")
        assert resolve_item_key("toastie") == ("toastie", "food")
        assert resolve_item_key("Babycino") == ("babycino", "drink")

    def test_legacy_resolve_drink_key_still_works(self):
        """Backwards-compatible wrapper."""
        assert resolve_drink_key("latte") == "latte"
        assert resolve_drink_key("12oz/Medium") == "latte"
        assert resolve_drink_key("TOASTIE") == "toastie"


class TestResolveModifierKey:
    def test_exact_match(self):
        assert resolve_modifier_key("oat") == "alt_milk"
        assert resolve_modifier_key("extra shot") == "extra_shot"
        assert resolve_modifier_key("decaf") == "decaf"

    def test_case_insensitive(self):
        assert resolve_modifier_key("Oat Milk") == "alt_milk"
        assert resolve_modifier_key("DECAF") == "decaf"

    def test_substring_match(self):
        assert resolve_modifier_key("Add Vanilla Syrup") == "syrup"
        assert resolve_modifier_key("Almond Milk Please") == "alt_milk"

    def test_unknown_returns_none(self):
        assert resolve_modifier_key("extra foam") is None
        assert resolve_modifier_key("takeaway") is None


# ============================================================
# Item Workload Calculation (Sections 5.1-5.3)
# ============================================================


class TestCalculateItemWorkload:
    def test_basic_espresso(self):
        result = calculate_item_workload("Espresso", [], position_in_order=1)
        assert result["workload_units"] == 1.0
        assert result["drink_key"] == "espresso"
        assert result["is_milk_drink"] is False

    def test_basic_latte(self):
        result = calculate_item_workload("Latte", [], position_in_order=1)
        assert result["workload_units"] == 2.5
        assert result["drink_key"] == "latte"
        assert result["is_milk_drink"] is True

    def test_flat_white(self):
        result = calculate_item_workload("Flat White", [], position_in_order=1)
        assert result["workload_units"] == 2.8
        assert result["is_milk_drink"] is True

    def test_alt_milk_modifier(self):
        """Oat milk adds +0.5 units (Section 5.2)."""
        result = calculate_item_workload("Latte", [{"name": "Oat Milk"}], position_in_order=1)
        assert result["workload_units"] == 3.0  # 2.5 + 0.5
        assert "alt_milk" in result["applied_modifiers"]

    def test_decaf_modifier(self):
        """Decaf adds +0.3 units (Section 5.2)."""
        result = calculate_item_workload("Cappuccino", [{"name": "Decaf"}], position_in_order=1)
        assert result["workload_units"] == 2.8  # 2.5 + 0.3

    def test_position_1_multiplier(self):
        """1st drink: 1.0x (Section 5.3)."""
        result = calculate_item_workload("Latte", [], position_in_order=1)
        assert result["position_multiplier"] == 1.0
        assert result["workload_units"] == 2.5

    def test_position_2_multiplier(self):
        """2nd drink: 1.3x (Section 5.3)."""
        result = calculate_item_workload("Latte", [], position_in_order=2)
        assert result["position_multiplier"] == 1.3
        assert result["workload_units"] == 3.25  # 2.5 * 1.3

    def test_position_3_multiplier(self):
        """3rd+ drink: 1.6x (Section 5.3)."""
        result = calculate_item_workload("Latte", [], position_in_order=3)
        assert result["position_multiplier"] == 1.6
        assert result["workload_units"] == 4.0  # 2.5 * 1.6

    def test_position_4_uses_3rd_multiplier(self):
        """4th drink also uses 1.6x."""
        result = calculate_item_workload("Latte", [], position_in_order=4)
        assert result["position_multiplier"] == 1.6

    def test_multiple_modifiers(self):
        """Extra shot + syrup."""
        result = calculate_item_workload(
            "Latte",
            [{"name": "Extra Shot"}, {"name": "Vanilla"}],
            position_in_order=1,
        )
        # 2.5 + 0.5 (extra shot) + 0.4 (syrup) = 3.4
        assert result["workload_units"] == 3.4
        assert "extra_shot" in result["applied_modifiers"]
        assert "syrup" in result["applied_modifiers"]

    def test_food_item_no_position_penalty(self):
        """Food items always get 1.0x multiplier regardless of position."""
        result = calculate_item_workload("TOASTIE", [], position_in_order=3)
        assert result["category"] == "food"
        assert result["is_drink"] is False
        assert result["is_milk_drink"] is False
        assert result["position_multiplier"] == 1.0
        assert result["workload_units"] == 1.8

    def test_retail_item_minimal_score(self):
        """Retail items have low workload scores."""
        result = calculate_item_workload("Fiji Water", [], position_in_order=1)
        assert result["category"] == "retail"
        assert result["is_drink"] is False
        assert result["workload_units"] == 0.2

    def test_babycino_score(self):
        """Babycino is a drink with low score."""
        result = calculate_item_workload("Babycino", [], position_in_order=1)
        assert result["category"] == "drink"
        assert result["is_drink"] is True
        assert result["workload_units"] == 0.5

    def test_square_size_based_items(self):
        """Square sends sizes, not drink names."""
        result_med = calculate_item_workload("12oz/Medium", [], position_in_order=1)
        assert result_med["drink_key"] == "latte"
        assert result_med["category"] == "drink"

        result_small = calculate_item_workload("8oz/Small", [], position_in_order=1)
        assert result_small["drink_key"] == "flat_white"

        result_iced = calculate_item_workload("16oz/ICED MEDIUM", [], position_in_order=1)
        assert result_iced["drink_key"] == "iced_latte"

    def test_large_size_auto_modifier(self):
        """16oz/Large should auto-apply the 'large' modifier."""
        result = calculate_item_workload("16oz/Large", [], position_in_order=1)
        assert "large" in result["applied_modifiers"]
        # latte (2.5) + large (0.2) = 2.7
        assert result["workload_units"] == 2.7


# ============================================================
# Spec Section 5.4 Example: 2x Oat Latte + 1x Decaf Cap
# ============================================================


class TestSpecExample:
    """
    Spec Section 5.4:
        Order: 2× Oat Latte + 1× Decaf Cap
        Drink 1 (Oat Latte): 2.5 + 0.5 = 3.0, × 1.0 = 3.0
        Drink 2 (Oat Latte): 3.0, × 1.3 = 3.9
        Drink 3 (Decaf Cap): 2.5 + 0.3 = 2.8, × 1.6 = 4.48
        Total: 3.0 + 3.9 + 4.48 = 11.38 units
    """

    def test_spec_example_total(self, sample_parsed_order):
        result = calculate_order_workload(sample_parsed_order)
        assert result["total_workload_units"] == 11.38

    def test_spec_example_item_count(self, sample_parsed_order):
        result = calculate_order_workload(sample_parsed_order)
        assert result["item_count"] == 3  # 2 lattes + 1 cap (expanded)

    def test_spec_example_milk_count(self, sample_parsed_order):
        result = calculate_order_workload(sample_parsed_order)
        assert result["milk_drink_count"] == 3  # All are milk drinks

    def test_spec_example_individual_items(self, sample_parsed_order):
        result = calculate_order_workload(sample_parsed_order)
        items = result["line_items"]

        # Drink 1: Oat Latte @ position 1
        assert items[0]["workload"]["workload_units"] == 3.0
        assert items[0]["effective_position"] == 1

        # Drink 2: Oat Latte @ position 2
        assert items[1]["workload"]["workload_units"] == 3.9
        assert items[1]["effective_position"] == 2

        # Drink 3: Decaf Cap @ position 3
        assert items[2]["workload"]["workload_units"] == 4.48
        assert items[2]["effective_position"] == 3


# ============================================================
# Timeline Aggregation
# ============================================================


class TestAggregateTimeline:
    def test_single_order(self, sample_parsed_order):
        processed = calculate_order_workload(sample_parsed_order)
        timeline = aggregate_timeline([processed], interval_minutes=15)

        assert len(timeline) == 1
        assert timeline[0]["workload_units"] == 11.38
        assert timeline[0]["orders_count"] == 1
        assert timeline[0]["items_count"] == 3

    def test_multiple_orders_same_bucket(self):
        orders = [
            {
                "order_id": "o1",
                "closed_at": "2026-02-07T08:02:00+10:00",
                "line_items": [{"item_name": "Espresso", "quantity": 1, "modifiers": []}],
            },
            {
                "order_id": "o2",
                "closed_at": "2026-02-07T08:10:00+10:00",
                "line_items": [{"item_name": "Latte", "quantity": 1, "modifiers": []}],
            },
        ]
        processed = [calculate_order_workload(o) for o in orders]
        timeline = aggregate_timeline(processed, interval_minutes=15)

        assert len(timeline) == 1
        assert timeline[0]["orders_count"] == 2
        assert timeline[0]["workload_units"] == 3.5  # 1.0 + 2.5

    def test_orders_different_buckets(self):
        orders = [
            {
                "order_id": "o1",
                "closed_at": "2026-02-07T08:02:00+10:00",
                "line_items": [{"item_name": "Espresso", "quantity": 1, "modifiers": []}],
            },
            {
                "order_id": "o2",
                "closed_at": "2026-02-07T08:20:00+10:00",
                "line_items": [{"item_name": "Latte", "quantity": 1, "modifiers": []}],
            },
        ]
        processed = [calculate_order_workload(o) for o in orders]
        timeline = aggregate_timeline(processed, interval_minutes=15)

        assert len(timeline) == 2

    def test_empty_orders(self):
        timeline = aggregate_timeline([], interval_minutes=15)
        assert timeline == []


# ============================================================
# Wally Volume Calculation (Section 4.4)
# ============================================================


class TestCalculateWallyVolume:
    def test_spec_example(self):
        """
        Spec Section 4.4:
            52 drinks → 70% milk = 36 milk drinks
            Volume: (36 × 180 × 1.2) / 1000 = 7.78L → round up to 8L
        """
        result = calculate_wally_volume(52)
        assert result["predicted_milk_drinks"] == 36  # 52 * 0.70
        assert result["total_litres"] == 8

    def test_split_sums_to_total(self):
        result = calculate_wally_volume(52)
        split_total = sum(result["split"].values())
        assert split_total == result["total_litres"]

    def test_split_ratios(self):
        result = calculate_wally_volume(52)
        assert result["split"]["full_cream"] >= result["split"]["oat"]
        assert result["split"]["oat"] >= result["split"]["soy"]

    def test_zero_drinks(self):
        result = calculate_wally_volume(0)
        assert result["total_litres"] == 0
        assert result["predicted_milk_drinks"] == 0

    def test_small_order(self):
        result = calculate_wally_volume(5)
        assert result["total_litres"] >= 1
        assert result["predicted_milk_drinks"] == 3  # 5 * 0.70 = 3.5 → int = 3


# ============================================================
# 4-Layer Composite Prediction (Section 5.5)
# ============================================================


class TestPredictWorkload:
    def test_spec_example(self):
        """
        Spec Section 5.5 example:
            Recent avg: 51.3, YoY avg: 53.7, Friday factor: 1.12
            base = (51.3 * 0.60) + (53.7 * 0.25) + (51.3 * 1.12 * 0.15)
                 = 30.78 + 13.425 + 8.6184 = 52.8234
            Rounded to 52.8
        """
        result = predict_workload(
            recent_values=[52, 48, 55, 51, 49, 53],  # avg = 51.33
            yoy_values=[54, 56, 51],  # avg = 53.67
            dow_name="Friday",
            event_multiplier=1.0,
        )

        # Should be approximately 52.8
        assert 52.0 <= result["base_prediction"] <= 54.0
        assert result["dow_factor"] == 1.12
        assert result["event_multiplier"] == 1.0

    def test_rush_detection(self):
        """Rush threshold = baseline * 1.3."""
        result = predict_workload(
            recent_values=[100, 100, 100],
            yoy_values=[100, 100],
            dow_name="Monday",
            event_multiplier=2.0,  # Doubles the prediction above threshold
        )
        assert result["is_rush"] is True

    def test_no_rush_normal_conditions(self):
        result = predict_workload(
            recent_values=[50, 50, 50],
            yoy_values=[50, 50],
            dow_name="Monday",
            event_multiplier=1.0,
        )
        assert result["is_rush"] is False

    def test_confidence_high(self):
        """Stable data = high confidence (>=85%)."""
        result = predict_workload(
            recent_values=[50, 51, 49, 50, 51, 50],
            yoy_values=[50, 51],
            dow_name="Monday",
        )
        assert result["confidence"] >= 0.85
        assert result["confidence_label"] == "high"

    def test_confidence_low_sparse_data(self):
        """Very few data points = lower confidence."""
        result = predict_workload(
            recent_values=[50],
            yoy_values=[],
            dow_name="Monday",
        )
        assert result["confidence"] < 0.85
        assert result["confidence_label"] in ("medium", "low")

    def test_confidence_low_high_variance(self):
        """High variance in recent data lowers confidence."""
        result = predict_workload(
            recent_values=[10, 90, 15, 85, 20, 80],
            yoy_values=[50, 50],
            dow_name="Monday",
        )
        assert result["confidence"] <= 0.85

    def test_empty_recent_values(self):
        """Should handle empty recent data gracefully."""
        result = predict_workload(
            recent_values=[],
            yoy_values=[50, 50],
            dow_name="Monday",
        )
        assert result["recent_avg"] == 0.0

    def test_event_multiplier_applied(self):
        """Event multiplier should scale the final prediction."""
        normal = predict_workload(
            recent_values=[50, 50, 50],
            yoy_values=[50],
            dow_name="Monday",
            event_multiplier=1.0,
        )
        event = predict_workload(
            recent_values=[50, 50, 50],
            yoy_values=[50],
            dow_name="Monday",
            event_multiplier=1.18,
        )
        assert event["final_prediction"] > normal["final_prediction"]

    def test_sample_sizes_reported(self):
        result = predict_workload(
            recent_values=[50, 50, 50],
            yoy_values=[50, 50],
            dow_name="Monday",
        )
        assert result["sample_sizes"]["recent"] == 3
        assert result["sample_sizes"]["yoy"] == 2


# ============================================================
# Batch Processing Pipeline
# ============================================================


class TestProcessOrdersBatch:
    def test_batch_processing(self, sample_parsed_order):
        result = process_orders_batch([sample_parsed_order])

        assert result["summary"]["orders_count"] == 1
        assert result["summary"]["items_count"] == 3
        assert result["summary"]["total_workload_units"] == 11.38
        assert result["summary"]["milk_drink_count"] == 3
        assert result["summary"]["drink_count"] == 3
        assert result["summary"]["food_count"] == 0
        assert len(result["timeline"]) >= 1

    def test_empty_batch(self):
        result = process_orders_batch([])
        assert result["summary"]["orders_count"] == 0
        assert result["timeline"] == []

    def test_mixed_order_batch(self):
        """Order with drinks and food should separate counts."""
        order = {
            "order_id": "mixed-001",
            "closed_at": "2026-02-07T09:00:00+10:00",
            "line_items": [
                {"item_name": "12oz/Medium", "quantity": 1, "modifiers": []},
                {"item_name": "TOASTIE", "quantity": 1, "modifiers": []},
                {"item_name": "Fiji Water", "quantity": 1, "modifiers": []},
            ],
        }
        result = process_orders_batch([order])
        assert result["summary"]["drink_count"] == 1
        assert result["summary"]["food_count"] == 1
        assert result["summary"]["items_count"] == 3
