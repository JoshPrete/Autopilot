"""
Tests for delivery/sender.py - SMS Sender

Covers:
- Message building from action types
- Role-based routing
- Batch dispatch logic
- Fail Quiet behavior
"""

from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

import pytest

from delivery.sender import (
    _build_message,
    dispatch_recommendation,
    send_sms,
)


# ============================================================
# Message Builder
# ============================================================


class TestBuildMessage:
    def test_pre_rush_reminder(self):
        msg = _build_message(
            action_type="PRE_RUSH_REMINDER",
            details={
                "rush_start": "2026-02-07T08:47:00",
                "rush_end": "2026-02-07T09:32:00",
                "predicted_drinks": 52,
                "confidence_label": "high",
            },
            staff_names={},
        )
        assert msg is not None
        assert "Rush in 15 mins" in msg

    def test_switch_to_3p(self):
        msg = _build_message(
            action_type="SWITCH_TO_3P",
            details={"rush_start": "2026-02-07T08:47:00"},
            staff_names={"P1": "Sarah", "P2": "Tom", "P3": "Jess"},
        )
        assert "Switch to 3P" in msg
        assert "Sarah" in msg

    def test_wally_start(self):
        msg = _build_message(
            action_type="WALLY_START",
            details={"volume_litres": 8, "split": {"full_cream": 5, "oat": 2, "soy": 1}},
            staff_names={"P2": "Tom"},
        )
        assert "Tom: Start Wally cycling" in msg

    def test_p1_to_shots(self):
        msg = _build_message("P1_TO_SHOTS", {}, {})
        assert "Counter clear" in msg

    def test_delivery_override(self):
        msg = _build_message("DELIVERY_OVERRIDE", {}, {"P3": "Jessica"})
        assert "Jessica: Delivery priority NOW" in msg

    def test_delivery_priority(self):
        msg = _build_message("DELIVERY_PRIORITY", {}, {"P3": "Jessica"})
        assert "Jessica: Delivery priority NOW" in msg

    def test_rush_end(self):
        msg = _build_message("RUSH_END", {}, {})
        assert "Rush easing" in msg

    def test_end_of_day_feedback(self):
        msg = _build_message("END_OF_DAY_FEEDBACK", {}, {})
        assert "How was today's plan?" in msg

    def test_unknown_uses_message_field(self):
        msg = _build_message("CUSTOM_TYPE", {"message": "Custom action"}, {})
        assert msg == "Custom action"

    def test_unknown_no_message_returns_none(self):
        msg = _build_message("CUSTOM_TYPE", {}, {})
        assert msg is None


# ============================================================
# SMS Sending
# ============================================================


class TestSendSms:
    @patch("delivery.sender.settings")
    def test_no_twilio_config_returns_none(self, mock_settings):
        mock_settings.TWILIO_ACCOUNT_SID = ""
        mock_settings.TWILIO_AUTH_TOKEN = ""
        result = send_sms("+61412345678", "Test message")
        assert result is None

    @patch("delivery.sender.settings")
    def test_no_phone_number_returns_none(self, mock_settings):
        mock_settings.TWILIO_ACCOUNT_SID = "sid"
        mock_settings.TWILIO_AUTH_TOKEN = "token"
        mock_settings.TWILIO_PHONE_NUMBER = ""
        result = send_sms("+61412345678", "Test message")
        assert result is None

    @patch("delivery.sender.settings")
    def test_twilio_exception_returns_none(self, mock_settings):
        """Fail Quiet: exception → None, not raised."""
        mock_settings.TWILIO_ACCOUNT_SID = "sid"
        mock_settings.TWILIO_AUTH_TOKEN = "token"
        mock_settings.TWILIO_PHONE_NUMBER = "+61400000000"

        with patch("twilio.rest.Client", side_effect=Exception("Twilio down")):
            # Should not raise
            result = send_sms("+61412345678", "Test")
            assert result is None


# ============================================================
# Recommendation Dispatch
# ============================================================


class TestDispatchRecommendation:
    @patch("delivery.sender.send_to_role")
    def test_dispatch_maps_to_correct_role(self, mock_send):
        mock_send.return_value = [{"delivered": True}]

        rec = {
            "action_type": "WALLY_START",
            "owner_role": "P2",
            "action_details": {
                "volume_litres": 8,
                "split": {"full_cream": 5, "oat": 2, "soy": 1},
            },
        }

        dispatch_recommendation("site-1", rec, {"P2": "Tom"})
        mock_send.assert_called_once()
        call_args = mock_send.call_args
        assert call_args[0][1] == "P2"  # role argument

    @patch("delivery.sender.send_to_role")
    def test_dispatch_unknown_type_no_send(self, mock_send):
        rec = {
            "action_type": "UNKNOWN_TYPE",
            "owner_role": "MANAGER",
            "action_details": {},
        }

        result = dispatch_recommendation("site-1", rec)
        mock_send.assert_not_called()
        assert result == []
