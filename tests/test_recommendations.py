"""
Tests for models/recommendations.py - State Machine & Recommendations

Covers:
- State machine transitions
- Rush recommendation generation (Section 3.3 timings)
- Pre-rush checklist
- Role assignments
- Wally plan builder
"""

from datetime import datetime
from unittest.mock import patch, MagicMock

import pytest

from models.recommendations import (
    StateMachine,
    build_wally_plan,
    generate_pre_rush_checklist,
    generate_rush_recommendations,
    get_role_assignments,
)
from tests.conftest import TEST_SITE_ID, TEST_PREDICTION_ID


# ============================================================
# State Machine (Section 3.1)
# ============================================================


class TestStateMachine:
    def test_initial_state(self):
        sm = StateMachine()
        assert sm.current_state == "S2P_STANDARD"
        assert sm.staff_count == 2

    def test_custom_initial_state(self):
        sm = StateMachine("S3P_RUSH")
        assert sm.current_state == "S3P_RUSH"
        assert sm.staff_count == 3

    def test_invalid_initial_state(self):
        with pytest.raises(ValueError, match="Invalid state"):
            StateMachine("INVALID")

    def test_transition(self):
        sm = StateMachine("S2P_STANDARD")
        record = sm.transition("S3P_RUSH", reason="Rush detected")

        assert sm.current_state == "S3P_RUSH"
        assert record["from_state"] == "S2P_STANDARD"
        assert record["to_state"] == "S3P_RUSH"
        assert record["reason"] == "Rush detected"
        assert record["new_staff_count"] == 3

    def test_transition_invalid_target(self):
        sm = StateMachine()
        with pytest.raises(ValueError, match="Invalid target state"):
            sm.transition("INVALID")

    def test_history_tracking(self):
        sm = StateMachine("S2P_STANDARD")
        sm.transition("S2P_BUSY", "pressure building")
        sm.transition("S3P_RUSH", "rush detected")

        assert len(sm.history) == 2
        assert sm.history[0]["from_state"] == "S2P_STANDARD"
        assert sm.history[1]["to_state"] == "S3P_RUSH"

    def test_resolve_state_from_staff(self):
        sm = StateMachine()
        assert sm.resolve_state_from_staff(1) == "S1P_SURVIVAL"
        assert sm.resolve_state_from_staff(2, is_rush=False) == "S2P_STANDARD"
        assert sm.resolve_state_from_staff(2, is_rush=True) == "S2P_BUSY"
        assert sm.resolve_state_from_staff(3) == "S3P_RUSH"
        assert sm.resolve_state_from_staff(4) == "S4P_STANDARD"

    def test_purpose(self):
        sm = StateMachine("S1P_SURVIVAL")
        assert sm.purpose == "Keep flow alive"

    def test_staff_count_all_states(self):
        assert StateMachine("S1P_SURVIVAL").staff_count == 1
        assert StateMachine("S2P_STANDARD").staff_count == 2
        assert StateMachine("S2P_BUSY").staff_count == 2
        assert StateMachine("S3P_RUSH").staff_count == 3
        assert StateMachine("S4P_STANDARD").staff_count == 4


# ============================================================
# Rush Recommendations (Section 3.3)
# ============================================================


class TestGenerateRushRecommendations:
    @patch("models.recommendations.store_recommendation")
    def test_generates_6_recommendations(self, mock_store, sample_rush_window, staff_names):
        mock_store.return_value = "mock-rec-id"

        recs = generate_rush_recommendations(
            site_id=TEST_SITE_ID,
            prediction_id=TEST_PREDICTION_ID,
            rush_window=sample_rush_window,
            staff_names=staff_names,
        )

        assert len(recs) == 6

    @patch("models.recommendations.store_recommendation")
    def test_recommendation_types(self, mock_store, sample_rush_window, staff_names):
        mock_store.return_value = "mock-rec-id"

        recs = generate_rush_recommendations(
            site_id=TEST_SITE_ID,
            prediction_id=TEST_PREDICTION_ID,
            rush_window=sample_rush_window,
            staff_names=staff_names,
        )

        types = [r["action_type"] for r in recs]
        assert "PRE_RUSH_REMINDER" in types
        assert "WALLY_START" in types
        assert "SWITCH_TO_3P" in types
        assert "P3_PREP" in types
        assert "DELIVERY_PRIORITY" in types
        assert "RUSH_END" in types

    @patch("models.recommendations.store_recommendation")
    def test_timing_order(self, mock_store, sample_rush_window, staff_names):
        """Recommendations should follow chronological order."""
        mock_store.return_value = "mock-rec-id"

        recs = generate_rush_recommendations(
            site_id=TEST_SITE_ID,
            prediction_id=TEST_PREDICTION_ID,
            rush_window=sample_rush_window,
            staff_names=staff_names,
        )

        timings = [r["action_timing"] for r in recs]
        # PRE_RUSH_REMINDER (T-15) should be earliest
        assert timings[0] <= timings[1]  # Reminder <= Wally start

    @patch("models.recommendations.store_recommendation")
    def test_owner_roles(self, mock_store, sample_rush_window, staff_names):
        mock_store.return_value = "mock-rec-id"

        recs = generate_rush_recommendations(
            site_id=TEST_SITE_ID,
            prediction_id=TEST_PREDICTION_ID,
            rush_window=sample_rush_window,
            staff_names=staff_names,
        )

        by_type = {r["action_type"]: r for r in recs}
        assert by_type["PRE_RUSH_REMINDER"]["owner_role"] == "MANAGER"
        assert by_type["WALLY_START"]["owner_role"] == "P2"
        assert by_type["SWITCH_TO_3P"]["owner_role"] == "MANAGER"
        assert by_type["P3_PREP"]["owner_role"] == "P3"
        assert by_type["DELIVERY_PRIORITY"]["owner_role"] == "P3"
        assert by_type["RUSH_END"]["owner_role"] == "MANAGER"

    @patch("models.recommendations.store_recommendation")
    def test_staff_names_in_messages(self, mock_store, sample_rush_window, staff_names):
        mock_store.return_value = "mock-rec-id"

        recs = generate_rush_recommendations(
            site_id=TEST_SITE_ID,
            prediction_id=TEST_PREDICTION_ID,
            rush_window=sample_rush_window,
            staff_names=staff_names,
        )

        by_type = {r["action_type"]: r for r in recs}

        # P2 name in Wally message
        wally_msg = by_type["WALLY_START"]["action_details"]["message"]
        assert "Tom" in wally_msg

        # P3 name in prep message
        prep_msg = by_type["P3_PREP"]["action_details"]["message"]
        assert "Jessica" in prep_msg

    @patch("models.recommendations.store_recommendation")
    def test_without_staff_names(self, mock_store, sample_rush_window):
        """Should use role labels when names not provided."""
        mock_store.return_value = "mock-rec-id"

        recs = generate_rush_recommendations(
            site_id=TEST_SITE_ID,
            prediction_id=TEST_PREDICTION_ID,
            rush_window=sample_rush_window,
        )

        by_type = {r["action_type"]: r for r in recs}
        wally_msg = by_type["WALLY_START"]["action_details"]["message"]
        assert "P2" in wally_msg


# ============================================================
# Pre-Rush Checklist
# ============================================================


class TestPreRushChecklist:
    def test_basic_checklist(self, sample_rush_window):
        checklist = generate_pre_rush_checklist(sample_rush_window)

        assert "Grind 2kg beans" in checklist
        assert "Check Bar 2 steam wand" in checklist
        assert "Clear dish station" in checklist
        assert "Brief team (use this sheet)" in checklist

    def test_oat_milk_restock(self, sample_rush_window):
        checklist = generate_pre_rush_checklist(sample_rush_window)
        restock_items = [c for c in checklist if "Restock" in c]
        assert len(restock_items) == 1
        assert "oat milk" in restock_items[0]

    def test_minimum_items(self, sample_rush_window):
        checklist = generate_pre_rush_checklist(sample_rush_window)
        assert len(checklist) >= 4


# ============================================================
# Role Assignments
# ============================================================


class TestRoleAssignments:
    def test_3p_rush_assignments(self, staff_names):
        assignments = get_role_assignments(staff_names, "S3P_RUSH")

        assert len(assignments) == 3
        roles = [a["role"] for a in assignments]
        assert "P1" in roles
        assert "P2" in roles
        assert "P3" in roles

    def test_2p_standard_assignments(self, staff_names):
        assignments = get_role_assignments(staff_names, "S2P_STANDARD")
        assert len(assignments) == 2

    def test_staff_names_in_assignments(self, staff_names):
        assignments = get_role_assignments(staff_names, "S3P_RUSH")
        by_role = {a["role"]: a for a in assignments}

        assert by_role["P1"]["name"] == "Sarah"
        assert by_role["P2"]["name"] == "Tom"
        assert by_role["P3"]["name"] == "Jessica"

    def test_role_responsibilities(self, staff_names):
        assignments = get_role_assignments(staff_names, "S3P_RUSH")
        by_role = {a["role"]: a for a in assignments}

        assert "Front" in by_role["P1"]["primary"]
        assert "Shots" in by_role["P2"]["primary"]
        assert "Delivery" in by_role["P3"]["primary"]


# ============================================================
# Wally Plan Builder
# ============================================================


class TestBuildWallyPlan:
    def test_no_rush_windows(self):
        plan = build_wally_plan([])
        assert plan["mode"] == "OFF"
        assert plan["batches"] == []

    def test_single_rush(self, sample_rush_window):
        plan = build_wally_plan([sample_rush_window])

        assert plan["owner"] == "P2"
        assert plan["total_litres"] == 8
        assert len(plan["batches"]) == 1
        assert plan["batches"][0]["batch_number"] == 1

    def test_multiple_rushes_cycling_mode(self, sample_rush_window):
        rush2 = {**sample_rush_window}
        rush2["start"] = "2026-02-07T12:12:00"
        rush2["end"] = "2026-02-07T12:48:00"

        plan = build_wally_plan([sample_rush_window, rush2])

        assert plan["mode"] == "CYCLING"
        assert len(plan["batches"]) == 2
        assert plan["total_litres"] == 16  # 8 + 8

    def test_long_rush_cycling_mode(self):
        rush = {
            "start": "2026-02-07T08:00:00",
            "end": "2026-02-07T09:00:00",
            "duration_minutes": 60,
            "predicted_drinks": 52,
            "wally_volume_litres": 8,
            "wally_split": {"full_cream": 5, "oat": 2, "soy": 1},
            "wally_start_time": "2026-02-07T07:48:00",
        }
        plan = build_wally_plan([rush])
        assert plan["mode"] == "CYCLING"
