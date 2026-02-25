"""
Tests for daily pipeline scenarios using golden fixtures.

Each fixture represents a realistic pipeline outcome. Tests verify
that build_run_report correctly classifies overall status, degraded
services, and trust metrics for each scenario.
"""

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from scripts.daily_autopilot import build_run_report

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _load_fixture(name: str) -> dict:
    path = FIXTURES_DIR / f"{name}.json"
    return json.loads(path.read_text())


# ============================================================
# build_run_report tests using golden fixtures
# ============================================================

SITE_ID = "00000000-0000-0000-0000-000000000001"
SITE_NAME = "Clubhouse Nundah"
RUN_DATE = date(2026, 2, 23)
STARTED_AT = datetime(2026, 2, 23, 17, 0, 0)


class TestHappyPath:
    def test_overall_status_is_ok(self):
        fixture = _load_fixture("happy_path")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        assert report["overall_status"] == "ok"
        assert report["degraded_services"] == []
        assert report["errors"] == []

    def test_trust_metrics_populated(self):
        fixture = _load_fixture("happy_path")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        trust = report["trust"]
        assert trust["prediction_id"] == "00000000-0000-0000-0000-000000000099"
        assert trust["total_drinks"] == 287
        assert trust["rush_count"] == 2
        assert trust["sms_sent"] == 1
        assert trust["sms_degraded"] is False
        assert trust["orders_ingested"] == 142
        assert trust["data_quality_status"] == "clear"

    def test_report_metadata(self):
        fixture = _load_fixture("happy_path")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        assert report["site_id"] == SITE_ID
        assert report["site_name"] == SITE_NAME
        assert report["run_date"] == "2026-02-23"
        assert report["step"] == "all"
        assert report["dry_run"] is False
        assert report["duration_ms"] >= 0


class TestWeatherDegraded:
    def test_still_ok_without_weather(self):
        """Weather failure doesn't degrade the pipeline — it's already fail-quiet in generate_prediction."""
        fixture = _load_fixture("weather_degraded")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        assert report["overall_status"] == "ok"
        assert report["errors"] == []


class TestDeputyDown:
    def test_deputy_error_reported(self):
        fixture = _load_fixture("deputy_down")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        # Deputy error makes overall status "error" level
        assert "deputy" in report["errors"]
        assert report["steps"]["deputy"]["status"] == "error"

    def test_prediction_still_ok(self):
        fixture = _load_fixture("deputy_down")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        assert report["steps"]["predict"]["status"] == "ok"
        assert report["trust"]["total_drinks"] == 310


class TestSmsFailed:
    def test_sms_degraded_flag(self):
        fixture = _load_fixture("sms_failed")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        # Predict step itself is still ok (plan was generated)
        assert report["steps"]["predict"]["status"] == "ok"
        assert report["trust"]["sms_degraded"] is True
        assert report["trust"]["sms_sent"] == 0
        assert report["trust"]["plan_length"] == 1050

    def test_overall_still_ok(self):
        """SMS failure is tracked in trust metrics, not as a step error."""
        fixture = _load_fixture("sms_failed")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        assert report["overall_status"] == "ok"


class TestPartialIngestBlocked:
    def test_prediction_skipped(self):
        fixture = _load_fixture("partial_ingest_blocked")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        assert report["steps"]["predict"]["status"] == "skipped"
        assert report["steps"]["predict"]["reason"] == "data_quality_flag"

    def test_overall_degraded(self):
        fixture = _load_fixture("partial_ingest_blocked")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        assert report["overall_status"] == "degraded"
        assert "predict" in report["degraded_services"]
        assert "intelligence" in report["degraded_services"]

    def test_no_trust_prediction_metrics(self):
        """When prediction is skipped, trust metrics should not contain prediction data."""
        fixture = _load_fixture("partial_ingest_blocked")
        results = {
            k: v
            for k, v in fixture.items()
            if k
            not in (
                "description",
                "expected_overall_status",
                "expected_degraded",
                "expected_errors",
            )
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", False, results, STARTED_AT)
        assert "prediction_id" not in report["trust"]
        assert "total_drinks" not in report["trust"]


class TestDryRun:
    def test_dry_run_steps_not_degraded(self):
        """Steps skipped due to dry_run should not be classified as degraded."""
        results = {
            "ingest": {"status": "ok", "orders": 100, "items": 200, "workload_units": 500.0},
            "deputy": {"status": "skipped", "reason": "not_configured"},
            "predict": {
                "status": "ok",
                "prediction_id": "test",
                "total_drinks": 200,
                "rush_count": 1,
                "sms_sent": 0,
                "sms_degraded": False,
                "plan_length": 800,
            },
            "intelligence": {"status": "skipped", "reason": "dry_run"},
        }
        report = build_run_report(SITE_ID, SITE_NAME, RUN_DATE, "all", True, results, STARTED_AT)
        assert report["overall_status"] == "ok"
        assert report["degraded_services"] == []


class TestSmsGuardrail:
    def test_sms_exception_does_not_crash_step_predict(self, monkeypatch):
        """step_predict should catch SMS exceptions and set sms_degraded=True."""
        from scripts import daily_autopilot

        monkeypatch.setattr(
            "scripts.daily_autopilot.get_data_quality_flags",
            lambda **_: [],
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.generate_prediction",
            lambda **_: {
                "prediction_id": "pred-1",
                "total_predicted_drinks": 200,
                "rush_count": 1,
                "rush_windows": [],
                "confidence_label": "high",
                "staffing_mode": "2P",
                "forecast": {"forecast_date": "2026-02-24", "day_name": "Tuesday"},
                "weather": None,
            },
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.generate_daily_recommendations",
            lambda **_: [],
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.get_prediction",
            lambda *_: None,
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.generate_tomorrow_plan",
            lambda **_: "PLAN TEXT",
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.store_prediction_plan_snapshot",
            lambda *_: None,
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.send_tomorrow_plan_sms",
            lambda *_, **__: (_ for _ in ()).throw(ConnectionError("Twilio down")),
        )

        result = daily_autopilot.step_predict(
            site_id="site-1",
            site_name="Clubhouse",
            run_date=date(2026, 2, 23),
            dry_run=False,
        )

        assert result["status"] == "ok"
        assert result["sms_degraded"] is True
        assert result["sms_sent"] == 0
        assert result["plan_length"] == len("PLAN TEXT")


class TestRecommendationGuardrail:
    def test_recommendation_exception_does_not_crash_step_predict(self, monkeypatch):
        """step_predict should catch recommendation generation exceptions."""
        from scripts import daily_autopilot

        monkeypatch.setattr(
            "scripts.daily_autopilot.get_data_quality_flags",
            lambda **_: [],
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.generate_prediction",
            lambda **_: {
                "prediction_id": "pred-1",
                "total_predicted_drinks": 200,
                "rush_count": 1,
                "rush_windows": [],
                "confidence_label": "high",
                "staffing_mode": "2P",
                "forecast": {"forecast_date": "2026-02-24", "day_name": "Tuesday"},
                "weather": None,
            },
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.generate_daily_recommendations",
            lambda **_: (_ for _ in ()).throw(RuntimeError("DB connection lost")),
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.get_prediction",
            lambda *_: None,
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.generate_tomorrow_plan",
            lambda **_: "PLAN TEXT",
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.store_prediction_plan_snapshot",
            lambda *_: None,
        )
        monkeypatch.setattr(
            "scripts.daily_autopilot.send_tomorrow_plan_sms",
            lambda *_, **__: [{"delivered": True}],
        )

        result = daily_autopilot.step_predict(
            site_id="site-1",
            site_name="Clubhouse",
            run_date=date(2026, 2, 23),
            dry_run=False,
        )

        assert result["status"] == "ok"
        assert result["recommendations"] == 0
        assert result["sms_sent"] == 1
