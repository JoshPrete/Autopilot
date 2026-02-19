from datetime import date

from analysis.workflow import analyze_workflow, generate_roster_change_plan


def test_analyze_workflow_returns_bottlenecks_and_scenarios(monkeypatch):
    monkeypatch.setattr(
        "analysis.workflow.get_daily_efficiency_snapshot",
        lambda *_args, **_kwargs: {
            "intervals": [
                {
                    "interval_start": "2026-02-19T08:00:00",
                    "staff_on": 2,
                    "orders_count": 5,
                    "revenue_cents": 18500,
                    "workload_units": 8.2,
                    "workload_per_staff": 4.1,
                    "status": "understaffed",
                },
                {
                    "interval_start": "2026-02-19T10:15:00",
                    "staff_on": 3,
                    "orders_count": 1,
                    "revenue_cents": 700,
                    "workload_units": 1.8,
                    "workload_per_staff": 0.6,
                    "status": "overstaffed",
                },
            ]
        },
    )

    result = analyze_workflow("site-1", date(2026, 2, 19))

    assert result["summary"]["intervals_analyzed"] == 2
    assert "workflow_roles" in result
    assert len(result["high_impact_intervals"]) >= 1
    top = result["high_impact_intervals"][0]
    assert top["bottleneck"]["type"] in (
        "service_counter_queue",
        "milk_finishing_capacity",
        "shot_to_finish_handoff",
        "idle_labor",
        "none",
    )
    assert len(top["scenarios"]) == 4
    assert {s["staff_count"] for s in top["scenarios"]} == {1, 2, 3, 4}


def test_analyze_workflow_state_distribution(monkeypatch):
    monkeypatch.setattr(
        "analysis.workflow.get_daily_efficiency_snapshot",
        lambda *_args, **_kwargs: {
            "intervals": [
                {"interval_start": "2026-02-19T07:00:00", "staff_on": 1, "workload_units": 2.0, "status": "balanced"},
                {"interval_start": "2026-02-19T07:15:00", "staff_on": 2, "workload_units": 4.0, "status": "balanced"},
                {"interval_start": "2026-02-19T07:30:00", "staff_on": 3, "workload_units": 6.0, "status": "balanced"},
                {"interval_start": "2026-02-19T07:45:00", "staff_on": 4, "workload_units": 8.0, "status": "balanced"},
            ]
        },
    )
    result = analyze_workflow("site-1", date(2026, 2, 19))
    dist = result["summary"]["state_distribution"]
    assert dist["1p"] == 1
    assert dist["2p"] == 1
    assert dist["3p"] == 1
    assert dist["4p_plus"] == 1


def test_generate_roster_change_plan_assigns_roles(monkeypatch):
    monkeypatch.setattr(
        "analysis.workflow.optimize_shifts_range",
        lambda *_args, **_kwargs: {
            "summary": {"days_with_predictions": 2, "days_without_predictions": 0},
            "weekly_templates": [
                {
                    "day_of_week": "Mon",
                    "status": "ok",
                    "template_shifts": [{"start_hour": 6, "end_hour": 10}, {"start_hour": 7, "end_hour": 11}],
                }
            ],
            "daily": [
                {
                    "target_date": "2026-02-23",  # Monday
                    "status": "ok",
                    "recommended_shifts": [{"shift_label": "L1-1"}, {"shift_label": "L1-2"}],
                    "summary": {
                        "recommended_shift_count": 2,
                        "recommended_total_hours": 8.0,
                        "estimated_labor_delta_cents": -1200,
                    },
                },
                {
                    "target_date": "2026-02-24",
                    "status": "no_prediction",
                    "summary": {"recommended_total_hours": 0, "estimated_labor_delta_cents": 0},
                },
            ],
        },
    )

    plan = generate_roster_change_plan("site-1", start_date=date(2026, 2, 23), days=14)
    assert plan["summary"]["days_with_predictions"] == 2
    assert len(plan["days_plan"]) == 2
    d0 = plan["days_plan"][0]
    assert d0["workflow_mode"] == "2p"
    assert len(d0["role_assignments"]) == 2
    d1 = plan["days_plan"][1]
    assert d1["status"] == "no_prediction"
