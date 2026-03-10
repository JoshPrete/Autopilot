from datetime import date

from analysis.shift_optimizer import optimize_shifts, optimize_shifts_range


def test_optimize_shifts_no_prediction(monkeypatch):
    monkeypatch.setattr(
        "analysis.shift_optimizer._parse_hourly_from_prediction", lambda *_args, **_kwargs: []
    )

    out = optimize_shifts("site-1", date(2026, 2, 19))
    assert out["status"] == "no_prediction"
    assert out["recommended_shifts"] == []


def test_optimize_shifts_generates_shift_blocks(monkeypatch):
    monkeypatch.setattr(
        "analysis.shift_optimizer._parse_hourly_from_prediction",
        lambda *_args, **_kwargs: [
            {"hour": 6, "predicted_workload": 2.1},
            {"hour": 7, "predicted_workload": 7.5},
            {"hour": 8, "predicted_workload": 8.2},
            {"hour": 9, "predicted_workload": 3.4},
        ],
    )
    monkeypatch.setattr(
        "analysis.shift_optimizer._estimate_hourly_rate", lambda *_args, **_kwargs: 30.0
    )
    monkeypatch.setattr(
        "analysis.shift_optimizer.get_rosters_for_date", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "analysis.shift_optimizer.list_operator_rules", lambda *_args, **_kwargs: []
    )

    out = optimize_shifts(
        "site-1",
        date(2026, 2, 19),
        target_wu_per_person=3.0,
        min_shift_hours=3,
        max_shift_hours=9,
        base_floor_staff=1,
    )

    assert out["status"] == "ok"
    assert out["summary"]["recommended_shift_count"] >= 1
    assert out["summary"]["recommended_total_hours"] > 0
    assert out["summary"]["recommended_labor_cents"] > 0
    assert len(out["hours"]) == 4


def test_optimize_shifts_applies_confirmed_staffing_constraints(monkeypatch):
    monkeypatch.setattr(
        "analysis.shift_optimizer._parse_hourly_from_prediction",
        lambda *_args, **_kwargs: [
            {"hour": 6, "predicted_workload": 1.0},
            {"hour": 7, "predicted_workload": 2.0},
            {"hour": 8, "predicted_workload": 2.0},
        ],
    )
    monkeypatch.setattr(
        "analysis.shift_optimizer._estimate_hourly_rate", lambda *_args, **_kwargs: 30.0
    )
    monkeypatch.setattr(
        "analysis.shift_optimizer.get_rosters_for_date", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        "analysis.shift_optimizer.list_operator_rules",
        lambda *_args, **_kwargs: [
            {
                "rule_id": "rule-1",
                "rule_type": "staffing_constraint",
                "payload": {
                    "day_of_week": "wednesday",
                    "daypart": "open",
                    "min_staff": 2,
                    "requires_senior": True,
                    "disallow_role_alone": "junior",
                },
            }
        ],
    )

    out = optimize_shifts("site-1", date(2026, 2, 18), base_floor_staff=1)

    assert out["status"] == "ok"
    assert out["hours"][0]["required_staff_smoothed"] == 2
    assert out["summary"]["staffing_constraints_applied_count"] == 1
    assert out["recommended_shifts"][0]["senior_required"] is True
    assert out["constraints"][0]["note"].startswith("Open: minimum 2 staff")


def test_optimize_shifts_range_builds_templates(monkeypatch):
    def _fake_opt(*_args, **kwargs):
        target_date = kwargs["target_date"]
        dow = target_date.weekday()
        if dow in (0, 1):  # Mon/Tue similar
            shifts = [
                {
                    "role_level": "L1",
                    "shift_label": "L1-1",
                    "start": f"{target_date.isoformat()}T06:00:00",
                    "end": f"{target_date.isoformat()}T10:00:00",
                    "duration_hours": 4,
                }
            ]
            status = "ok"
        else:
            shifts = []
            status = "no_prediction"
        return {
            "site_id": "site-1",
            "target_date": target_date.isoformat(),
            "status": status,
            "recommended_shifts": shifts,
            "constraints": (
                [{"note": "Open: minimum 2 staff", "requires_senior": True}] if shifts else []
            ),
            "summary": {
                "recommended_total_hours": 4.0 if shifts else 0.0,
                "recommended_labor_cents": 12000 if shifts else 0,
                "estimated_labor_delta_cents": -1000 if shifts else 0,
            },
        }

    monkeypatch.setattr("analysis.shift_optimizer.optimize_shifts", _fake_opt)
    monkeypatch.setattr(
        "analysis.shift_optimizer.get_bottom_line_scorecard",
        lambda *_args, **_kwargs: {
            "targets": {
                "primary_lever": {
                    "focus": "labor_efficiency",
                    "reason": "Labor % is above target while COGS is within range.",
                },
                "gaps": {
                    "weekly_labor_reduction_needed_cents": 10_000,
                    "weekly_revenue_needed_for_net_margin_target_cents": 0,
                },
                "current": {
                    "labor_pct": 31.0,
                    "margin_basis_net_margin_pct": 8.0,
                    "margin_basis_source": "xero_cash_truth",
                    "operating_overhead_cents": 21_000,
                },
            },
            "financial_truth": {"mode": "xero_factual", "coverage_days": 14},
        },
    )

    out = optimize_shifts_range("site-1", start_date=date(2026, 2, 16), days=7)
    assert out["summary"]["days_with_predictions"] == 2
    profitability = out["summary"]["profitability_context"]
    assert profitability["primary_lever"]["focus"] == "labor_efficiency"
    assert profitability["estimated_weekly_labor_savings_cents"] == 7_000
    assert profitability["labor_target_progress_ratio"] == 0.7
    mon = next(t for t in out["weekly_templates"] if t["day_of_week"] == "Mon")
    assert mon["status"] == "ok"
    assert len(mon["template_shifts"]) == 1
    assert mon["requires_senior_coverage"] is True
    assert mon["constraints"][0]["note"] == "Open: minimum 2 staff"
    assert "weekly labor reduction target" in mon["profitability_alignment"]["note"]
