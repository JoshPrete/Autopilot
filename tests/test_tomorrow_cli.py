import json
from datetime import date
from pathlib import Path

import pytest

from analysis.tomorrow_report import build_tomorrow_report_payload, render_tomorrow_report_markdown
from scripts.tomorrow_cli import TomorrowPlanBlockedError, ensure_tomorrow_inputs_ready


def test_tomorrow_report_golden_fixture():
    fixture_path = Path("tests/fixtures/tomorrow/golden_day.json")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    payload = build_tomorrow_report_payload(**fixture["input"])
    expected = fixture["expected"]

    assert payload["wage_risk"] == expected["wage_risk"]
    assert payload["recommended_action"] == expected["recommended_action"]
    assert payload["rush_windows"][0]["band"] == expected["first_band"]
    assert payload["rush_windows"][1]["band"] == expected["second_band"]

    markdown = render_tomorrow_report_markdown(payload)
    for snippet in expected["markdown_contains"]:
        assert snippet in markdown


def test_ensure_tomorrow_inputs_ready_blocks_partial_ingest(monkeypatch):
    monkeypatch.setattr(
        "scripts.tomorrow_cli.get_data_quality_flags",
        lambda **_kwargs: [
            {
                "flag_type": "partial_ingest",
                "severity": "high",
                "reason": "Orders volume materially below baseline",
            }
        ],
    )
    monkeypatch.setattr(
        "scripts.tomorrow_cli.get_day_ingest_diagnostics",
        lambda *_args, **_kwargs: {},
    )

    with pytest.raises(TomorrowPlanBlockedError) as exc:
        ensure_tomorrow_inputs_ready("site-1", date(2026, 2, 25))

    message = str(exc.value)
    assert "Tomorrow Plan blocked" in message
    assert "--step ingest --date 2026-02-25" in message
    assert "--step predict --date 2026-02-25" in message
    assert "flags/partial-ingest?flag_date=2026-02-25" in message
