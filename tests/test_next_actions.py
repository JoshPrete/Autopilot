from datetime import date

from analysis.next_actions import generate_next_actions, persist_next_actions


def test_generate_next_actions_returns_ranked_actions(monkeypatch):
    monkeypatch.setattr(
        "analysis.next_actions.backfill_realized_impacts",
        lambda *_args, **_kwargs: {"candidates": 0, "updated": 0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_daily_efficiency_snapshot",
        lambda *_args, **_kwargs: {
            "summary": {
                "deputy_staff_hours": 10.0,
                "deputy_labor_cost_cents": 10000,
                "revenue_per_labor_hour_cents": 6500,
                "labor_pct": 34.2,
            },
            "intervals": [
                {
                    "interval_start": "2026-02-18T08:00:00",
                    "status": "understaffed",
                    "revenue_cents": 12000,
                    "workload_units": 12.0,
                    "staff_delta": -1,
                },
                {
                    "interval_start": "2026-02-18T14:00:00",
                    "status": "overstaffed",
                    "revenue_cents": 800,
                    "workload_units": 1.2,
                    "staff_delta": 1,
                },
            ],
        },
    )
    monkeypatch.setattr(
        "analysis.next_actions.compute_item_margins",
        lambda *_args, **_kwargs: [
            {"item": "Matcha", "score_key": "matcha_complex", "margin_pct": 48.0, "qty": 50},
        ],
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_action_type_outcome_summary",
        lambda *_args, **_kwargs: {"adoption_rate": 0.7, "total_count": 12, "adopted_count": 8},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_data_health",
        lambda *_args, **_kwargs: {"status": "green", "score": 1.0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_bottom_line_scorecard",
        lambda *_args, **_kwargs: {
            "targets": {
                "primary_lever": {
                    "focus": "labor_efficiency",
                    "reason": "Labor % is above target while COGS is within range.",
                },
                "gaps": {
                    "weekly_labor_reduction_needed_cents": 80_000,
                    "weekly_cogs_reduction_needed_cents": 0,
                    "weekly_prime_cost_reduction_needed_cents": 80_000,
                    "weekly_revenue_needed_for_net_margin_target_cents": 0,
                },
            }
        },
    )
    monkeypatch.setattr(
        "analysis.next_actions.analyze_workflow",
        lambda *_args, **_kwargs: {
            "high_impact_intervals": [
                {
                    "interval_start": "2026-02-18T08:00:00",
                    "bottleneck": {"type": "milk_finishing_capacity", "severity": "high"},
                    "observed": {"staff_on": 2},
                    "scenarios": [
                        {"staff_count": 2, "estimated_net_delta_cents": 0, "labor_delta_cents": 0},
                        {
                            "staff_count": 3,
                            "estimated_net_delta_cents": 1800,
                            "labor_delta_cents": 650,
                        },
                    ],
                }
            ]
        },
    )

    result = generate_next_actions("site-1", target_date=date(2026, 2, 18), max_actions=8)
    assert result["summary"]["actions_generated"] >= 2
    assert (
        result["actions"][0]["expected_weekly_profit_uplift_cents"]
        >= result["actions"][-1]["expected_weekly_profit_uplift_cents"]
    )
    assert any(a["action_type"] == "ADD_STAFF_BLOCK" for a in result["actions"])
    assert any(a["action_type"] == "WORKFLOW_SHIFT_REALLOC" for a in result["actions"])
    assert "ranking_score_cents" in result["actions"][0]
    assert result["optimization_phase"] == result["summary"]["optimization_phase"]
    assert result["data_health"]["status"] == "green"
    assert result["actions"][0]["profitability_alignment"]["primary_lever"] == "labor_efficiency"
    assert result["actions"][0]["profitability_alignment"]["reason"]
    assert result["summary"]["profitability_goal"]["focus"] == "labor_efficiency"


def test_persist_next_actions_is_idempotent(monkeypatch):
    actions = [
        {"action_key": "k1", "action_type": "ADD_STAFF_BLOCK", "title": "x"},
        {"action_key": "k2", "action_type": "CUT_STAFF_BLOCK", "title": "y"},
    ]
    exists = {"k1": True, "k2": False}
    stored = []

    monkeypatch.setattr(
        "analysis.next_actions.recommendation_exists_for_action_key",
        lambda _sid, _atype, akey, _d: exists.get(akey, False),
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_rec_id_for_action_key",
        lambda _sid, _atype, akey, _d: f"existing-rec-{akey}",
    )
    monkeypatch.setattr(
        "analysis.next_actions.store_recommendation",
        lambda **kwargs: stored.append(kwargs) or "rec-1",
    )

    result = persist_next_actions("site-1", actions, target_date=date(2026, 2, 18))
    assert result["stored"] == 1
    assert result["skipped"] == 1
    assert len(stored) == 1
    assert "action_rec_map" in result
    assert result["action_rec_map"]["k1"] == "existing-rec-k1"  # skipped, looked up
    assert result["action_rec_map"]["k2"] == "rec-1"            # newly stored


def test_generate_next_actions_filters_low_confidence_when_data_health_red(monkeypatch):
    monkeypatch.setattr(
        "analysis.next_actions.backfill_realized_impacts",
        lambda *_args, **_kwargs: {"candidates": 0, "updated": 0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_daily_efficiency_snapshot",
        lambda *_args, **_kwargs: {
            "summary": {
                "deputy_staff_hours": 10.0,
                "deputy_labor_cost_cents": 10000,
                "revenue_per_labor_hour_cents": 6500,
                "labor_pct": 34.2,
            },
            "intervals": [
                {
                    "interval_start": "2026-02-18T08:00:00",
                    "status": "understaffed",
                    "revenue_cents": 12000,
                    "workload_units": 12.0,
                    "staff_delta": -1,
                }
            ],
        },
    )
    monkeypatch.setattr("analysis.next_actions.compute_item_margins", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "analysis.next_actions.get_action_type_outcome_summary",
        lambda *_args, **_kwargs: {"adoption_rate": 0.1, "total_count": 10, "adopted_count": 1},
    )
    monkeypatch.setattr(
        "analysis.next_actions.analyze_workflow",
        lambda *_args, **_kwargs: {"high_impact_intervals": []},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_data_health",
        lambda *_args, **_kwargs: {"status": "red", "score": 0.2},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_bottom_line_scorecard",
        lambda *_args, **_kwargs: {
            "targets": {"primary_lever": {"focus": "labor_efficiency"}, "gaps": {}}
        },
    )

    result = generate_next_actions("site-1", target_date=date(2026, 2, 18), max_actions=8)
    assert result["summary"]["data_health_status"] == "red"
    assert result["data_health"]["status"] == "red"
    assert result["summary"]["actions_generated"] == 0


def test_generate_next_actions_suppresses_non_positive_proven_actions(monkeypatch):
    monkeypatch.setattr(
        "analysis.next_actions.backfill_realized_impacts",
        lambda *_args, **_kwargs: {"candidates": 0, "updated": 0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_daily_efficiency_snapshot",
        lambda *_args, **_kwargs: {
            "summary": {
                "deputy_staff_hours": 10.0,
                "deputy_labor_cost_cents": 30000,
                "revenue_per_labor_hour_cents": 6500,
                "labor_pct": 34.2,
            },
            "intervals": [
                {
                    "interval_start": "2026-02-18T08:00:00",
                    "status": "understaffed",
                    "revenue_cents": 12000,
                    "workload_units": 12.0,
                    "staff_delta": -1,
                },
                {
                    "interval_start": "2026-02-18T14:00:00",
                    "status": "overstaffed",
                    "revenue_cents": 800,
                    "workload_units": 1.2,
                    "staff_delta": 1,
                },
            ],
        },
    )
    monkeypatch.setattr("analysis.next_actions.compute_item_margins", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "analysis.next_actions.analyze_workflow",
        lambda *_args, **_kwargs: {"high_impact_intervals": []},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_data_health",
        lambda *_args, **_kwargs: {"status": "green", "score": 1.0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_bottom_line_scorecard",
        lambda *_args, **_kwargs: {
            "targets": {"primary_lever": {"focus": "labor_efficiency"}, "gaps": {}}
        },
    )

    def _memory(_sid, action_type, days=90):
        if action_type == "ADD_STAFF_BLOCK":
            return {
                "adoption_rate": 0.8,
                "total_count": 6,
                "realized_count": 3,
                "avg_realized_weekly_profit_delta_cents": -2500,
            }
        if action_type == "CUT_STAFF_BLOCK":
            return {
                "adoption_rate": 0.7,
                "total_count": 6,
                "realized_count": 3,
                "avg_realized_weekly_profit_delta_cents": 4200,
            }
        return {"adoption_rate": 0.5, "total_count": 0, "realized_count": 0}

    monkeypatch.setattr("analysis.next_actions.get_action_type_outcome_summary", _memory)

    result = generate_next_actions("site-1", target_date=date(2026, 2, 18), max_actions=8)

    assert all(a["action_type"] != "ADD_STAFF_BLOCK" for a in result["actions"])
    assert any(a["action_type"] == "CUT_STAFF_BLOCK" for a in result["actions"])
    gate = result["summary"]["proven_gate"]
    assert gate["suppressed_count"] >= 1
    assert "ADD_STAFF_BLOCK" in gate["suppressed_action_types"]


def test_generate_next_actions_sets_labor_efficiency_phase_when_labor_pct_high(monkeypatch):
    monkeypatch.setattr(
        "analysis.next_actions.backfill_realized_impacts",
        lambda *_args, **_kwargs: {"candidates": 0, "updated": 0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_daily_efficiency_snapshot",
        lambda *_args, **_kwargs: {
            "summary": {
                "deputy_staff_hours": 10.0,
                "deputy_labor_cost_cents": 30000,
                "revenue_per_labor_hour_cents": 6500,
                "labor_pct": 34.2,
            },
            "intervals": [
                {
                    "interval_start": "2026-02-18T14:00:00",
                    "status": "overstaffed",
                    "revenue_cents": 800,
                    "workload_units": 1.2,
                    "staff_delta": 1,
                },
            ],
        },
    )
    monkeypatch.setattr("analysis.next_actions.compute_item_margins", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "analysis.next_actions.analyze_workflow",
        lambda *_args, **_kwargs: {"high_impact_intervals": []},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_action_type_outcome_summary",
        lambda *_args, **_kwargs: {"adoption_rate": 0.7, "total_count": 12, "adopted_count": 8},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_data_health",
        lambda *_args, **_kwargs: {"status": "green", "score": 1.0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_bottom_line_scorecard",
        lambda *_args, **_kwargs: {
            "targets": {"primary_lever": {"focus": "labor_efficiency"}, "gaps": {}}
        },
    )

    result = generate_next_actions("site-1", target_date=date(2026, 2, 18), max_actions=8)
    assert result["summary"]["optimization_phase"] == "labor_efficiency"
    assert result["phase_reason"] == result["summary"]["phase_reason"]
    assert "above target band" in result["summary"]["phase_reason"]


def test_generate_next_actions_switches_to_revenue_growth_and_suppresses_cut_actions(monkeypatch):
    monkeypatch.setattr(
        "analysis.next_actions.backfill_realized_impacts",
        lambda *_args, **_kwargs: {"candidates": 0, "updated": 0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_daily_efficiency_snapshot",
        lambda *_args, **_kwargs: {
            "summary": {
                "deputy_staff_hours": 10.0,
                "deputy_labor_cost_cents": 30000,
                "revenue_per_labor_hour_cents": 6500,
                "labor_pct": 25.5,
            },
            "intervals": [
                {
                    "interval_start": "2026-02-18T08:00:00",
                    "status": "understaffed",
                    "revenue_cents": 12000,
                    "workload_units": 12.0,
                    "staff_delta": -1,
                },
                {
                    "interval_start": "2026-02-18T08:15:00",
                    "status": "understaffed",
                    "revenue_cents": 11000,
                    "workload_units": 11.0,
                    "staff_delta": -1,
                },
                {
                    "interval_start": "2026-02-18T14:00:00",
                    "status": "overstaffed",
                    "revenue_cents": 800,
                    "workload_units": 1.2,
                    "staff_delta": 1,
                },
            ],
        },
    )
    monkeypatch.setattr(
        "analysis.next_actions.compute_item_margins",
        lambda *_args, **_kwargs: [
            {"item": "Matcha", "score_key": "matcha_complex", "margin_pct": 48.0, "qty": 50},
        ],
    )
    monkeypatch.setattr(
        "analysis.next_actions.analyze_workflow",
        lambda *_args, **_kwargs: {"high_impact_intervals": []},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_action_type_outcome_summary",
        lambda *_args, **_kwargs: {"adoption_rate": 0.7, "total_count": 12, "adopted_count": 8},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_data_health",
        lambda *_args, **_kwargs: {"status": "green", "score": 1.0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_bottom_line_scorecard",
        lambda *_args, **_kwargs: {
            "targets": {
                "primary_lever": {
                    "focus": "revenue_growth",
                    "reason": "Prime cost is within target but net margin is still low; grow revenue.",
                },
                "gaps": {
                    "weekly_revenue_needed_for_net_margin_target_cents": 60_000,
                    "weekly_labor_reduction_needed_cents": 0,
                    "weekly_cogs_reduction_needed_cents": 0,
                    "weekly_prime_cost_reduction_needed_cents": 0,
                },
            }
        },
    )

    result = generate_next_actions("site-1", target_date=date(2026, 2, 18), max_actions=8)
    assert result["summary"]["optimization_phase"] == "revenue_growth"
    assert result["profitability_goal"]["focus"] == "revenue_growth"
    assert "Service-risk cap exceeded" in result["summary"]["phase_reason"]
    assert all(a["action_type"] != "CUT_STAFF_BLOCK" for a in result["actions"])
    assert any(a["action_type"] in ("ADD_STAFF_BLOCK", "PRICE_TEST_UP") for a in result["actions"])


def test_generate_next_actions_can_prioritize_cogs_control_over_labor_cut(monkeypatch):
    monkeypatch.setattr(
        "analysis.next_actions.backfill_realized_impacts",
        lambda *_args, **_kwargs: {"candidates": 0, "updated": 0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_daily_efficiency_snapshot",
        lambda *_args, **_kwargs: {
            "summary": {
                "deputy_staff_hours": 10.0,
                "deputy_labor_cost_cents": 10000,
                "revenue_per_labor_hour_cents": 6500,
                "labor_pct": 34.2,
            },
            "intervals": [
                {
                    "interval_start": "2026-02-18T14:00:00",
                    "status": "overstaffed",
                    "revenue_cents": 800,
                    "workload_units": 1.2,
                    "staff_delta": 1,
                }
            ],
        },
    )
    monkeypatch.setattr(
        "analysis.next_actions.compute_item_margins",
        lambda *_args, **_kwargs: [
            {"item": "Matcha", "score_key": "matcha_complex", "margin_pct": 48.0, "qty": 50},
        ],
    )
    monkeypatch.setattr(
        "analysis.next_actions.analyze_workflow",
        lambda *_args, **_kwargs: {"high_impact_intervals": []},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_action_type_outcome_summary",
        lambda *_args, **_kwargs: {"adoption_rate": 0.7, "total_count": 12, "adopted_count": 8},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_data_health",
        lambda *_args, **_kwargs: {"status": "green", "score": 1.0},
    )
    monkeypatch.setattr(
        "analysis.next_actions.get_bottom_line_scorecard",
        lambda *_args, **_kwargs: {
            "targets": {
                "primary_lever": {
                    "focus": "cogs_control",
                    "reason": "COGS % is above target while labor is within range.",
                },
                "gaps": {
                    "weekly_cogs_reduction_needed_cents": 120_000,
                    "weekly_labor_reduction_needed_cents": 0,
                    "weekly_prime_cost_reduction_needed_cents": 120_000,
                    "weekly_revenue_needed_for_net_margin_target_cents": 0,
                },
            }
        },
    )

    result = generate_next_actions("site-1", target_date=date(2026, 2, 18), max_actions=8)

    assert result["summary"]["profitability_goal"]["focus"] == "cogs_control"
    assert result["actions"][0]["action_type"] == "PRICE_TEST_UP"
    assert (
        result["actions"][0]["profitability_alignment"]["reason"]
        == "Targets COGS control by improving gross margin against the remaining COGS gap of $1,200/wk."
    )
