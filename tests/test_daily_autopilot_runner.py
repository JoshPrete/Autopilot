from datetime import date, datetime

from scripts import daily_autopilot


def test_step_predict_blocks_with_operator_message(monkeypatch):
    captured_alert = {}

    monkeypatch.setattr(
        "scripts.daily_autopilot.get_data_quality_flags",
        lambda **_kwargs: [
            {
                "flag_type": "partial_ingest",
                "severity": "high",
                "reason": "Orders volume materially below baseline",
            }
        ],
    )
    monkeypatch.setattr(
        "scripts.daily_autopilot.send_system_alert",
        lambda *_args, **kwargs: captured_alert.update(kwargs) or [],
    )

    result = daily_autopilot.step_predict(
        site_id="site-1",
        site_name="Clubhouse",
        run_date=date(2026, 2, 22),
        dry_run=False,
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "data_quality_flag"
    assert "Site: site-1" in result["operator_message"]
    assert "Date: 2026-02-22" in result["operator_message"]
    assert "--step ingest --date 2026-02-22" in result["operator_message"]
    assert "--step predict --date 2026-02-22" in result["operator_message"]
    assert "Downstream skipped" in result["operator_message"]
    assert captured_alert["site_name"] == "Clubhouse"
    assert captured_alert["run_date"] == "2026-02-22"
    assert isinstance(captured_alert["reasons"], list)


def test_step_predict_from_prediction_id_regenerates_without_recalc(monkeypatch):
    row = {
        "prediction_id": "pred-1",
        "forecast_date": date(2026, 2, 23),
        "generated_at": datetime(2026, 2, 22, 18, 0),
        "event_factor": 1.0,
        "confidence_score": 0.82,
        "actual_accuracy": None,
        "forecast_data": {
            "forecast": {"forecast_date": "2026-02-23", "day_name": "Monday"},
            "rush_windows": [],
            "rush_count": 0,
            "total_predicted_drinks": 180,
            "staffing_mode": "2P",
        },
        "rush_windows": [],
    }

    monkeypatch.setattr(
        "scripts.daily_autopilot.get_prediction_by_id",
        lambda *_args, **_kwargs: row,
    )
    monkeypatch.setattr(
        "scripts.daily_autopilot.generate_tomorrow_plan",
        lambda **_kwargs: "DETERMINISTIC PLAN",
    )

    result = daily_autopilot.step_predict_from_prediction_id(
        site_id="site-1",
        site_name="Clubhouse",
        prediction_id="pred-1",
        dry_run=True,
    )

    assert result["status"] == "ok"
    assert result["mode"] == "regenerated"
    assert result["prediction_id"] == "pred-1"
    assert result["forecast_date"] == "2026-02-23"
    assert result["sms_sent"] == 0


def test_step_predict_from_prediction_id_prefers_snapshot(monkeypatch):
    row = {
        "prediction_id": "pred-2",
        "forecast_date": date(2026, 2, 24),
        "generated_at": datetime(2026, 2, 23, 18, 0),
        "event_factor": 1.0,
        "confidence_score": 0.9,
        "actual_accuracy": None,
        "forecast_data": {
            "forecast": {"forecast_date": "2026-02-24", "day_name": "Tuesday"},
            "rush_windows": [],
            "rush_count": 0,
            "total_predicted_drinks": 165,
            "staffing_mode": "2P",
            "plan_snapshot_text": "SNAPSHOT PLAN TEXT",
        },
        "rush_windows": [],
    }

    monkeypatch.setattr(
        "scripts.daily_autopilot.get_prediction_by_id",
        lambda *_args, **_kwargs: row,
    )

    def _unexpected_render(**_kwargs):
        raise AssertionError("generate_tomorrow_plan should not be called when snapshot exists")

    monkeypatch.setattr(
        "scripts.daily_autopilot.generate_tomorrow_plan",
        _unexpected_render,
    )

    result = daily_autopilot.step_predict_from_prediction_id(
        site_id="site-1",
        site_name="Clubhouse",
        prediction_id="pred-2",
        dry_run=True,
    )

    assert result["status"] == "ok"
    assert result["mode"] == "regenerated"
    assert result["prediction_id"] == "pred-2"
    assert result["forecast_date"] == "2026-02-24"
    assert result["plan_length"] == len("SNAPSHOT PLAN TEXT")
