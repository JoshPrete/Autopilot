from app.chat import build_system_prompt, gather_chat_context


def _patch_common_chat_dependencies(monkeypatch):
    monkeypatch.setattr("app.chat.get_data_freshness", lambda *_args, **_kwargs: "2026-02-19")
    monkeypatch.setattr("app.chat.get_prediction", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "app.chat.get_rolling_accuracy", lambda *_args, **_kwargs: {"days_measured": 0}
    )
    monkeypatch.setattr("app.chat._get_upcoming_events", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_revenue_from_orders", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.get_roster_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_roster_for_date", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.has_real_cogs", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("data.xero.is_xero_configured", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(
        "app.chat._get_cogs_snapshot", lambda *_args, **_kwargs: {"total_items": 8, "real_items": 6}
    )
    monkeypatch.setattr("app.chat.get_recent_documents", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.get_intelligence_summary", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("app.chat.get_recent_insights", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.get_inventory_alerts", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.list_inventory_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.list_inventory_usage_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._has_roster_data", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("app.chat.get_staffing_vs_workload", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_predictions_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_daily_items_summary", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_top_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_item_counts_by_day", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_modifier_stats", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("app.chat._get_workload_timeline_recent", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.get_site", lambda *_args, **_kwargs: {"name": "Clubhouse"})
    monkeypatch.setattr("analysis.reporting.generate_weekly_review", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("app.chat._get_hourly_averages", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_trending_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_operational_benchmarks", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("app.chat._get_profitability_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_item_margins_context", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat._get_weather_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("app.chat.get_events_range", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.get_dow_pattern", lambda *_args, **_kwargs: {})
    monkeypatch.setattr("app.chat.get_learned_patterns", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "app.chat.get_bottom_line_scorecard",
        lambda *_args, **_kwargs: {
            "headline": "Net profit and labor efficiency are improving versus prior window.",
            "kpis": {
                "total_net_profit_cents": 120000,
                "net_margin_pct": 12.4,
                "avg_labor_pct": 29.1,
                "avg_revenue_per_labor_hour_cents": 6800,
            },
            "trend": {
                "deltas": {
                    "net_profit_cents": 14000,
                    "labor_pct_delta_pp": -1.3,
                    "revenue_per_labor_hour_delta_pct": 4.8,
                },
                "directions": {
                    "net_profit": "improving",
                    "labor_pct": "improving",
                    "revenue_per_labor_hour": "improving",
                },
            },
            "actions": {
                "recommendations_generated": 6,
                "recommendations_adopted": 4,
                "realized_actions": 3,
                "avg_realized_weekly_profit_delta_cents": 5200,
                "top_proven_action_types": [
                    {
                        "action_type": "CUT_STAFF_BLOCK",
                        "realized_count": 3,
                        "avg_realized_weekly_profit_delta_cents": 5200,
                    }
                ],
            },
            "financial_truth": {
                "mode": "xero_factual",
                "coverage_days": 24,
                "window_days": 30,
                "income_cents": 4500000,
                "expense_cents": 2980000,
                "net_cash_cents": 1520000,
            },
        },
    )


def test_gather_chat_context_includes_operator_intelligence(monkeypatch):
    _patch_common_chat_dependencies(monkeypatch)
    monkeypatch.setattr(
        "app.chat._get_recent_efficiency_context",
        lambda *_args, **_kwargs: {
            "date": "2026-02-19",
            "summary": {"intervals_analyzed": 32, "total_revenue_cents": 150000},
            "variance_summary": {
                "understaffed_intervals": 3,
                "overstaffed_intervals": 2,
                "no_staff_intervals": 0,
            },
            "peaks": {"mismatch": []},
        },
    )
    monkeypatch.setattr(
        "analysis.next_actions.generate_next_actions",
        lambda *_args, **_kwargs: {
            "actions": [{"title": "Trim 1 staff-hour", "expected_weekly_profit_uplift_cents": 7000}]
        },
    )
    monkeypatch.setattr(
        "app.chat._get_recent_recommendations",
        lambda *_args, **_kwargs: [{"title": "Cut staff block", "adopted": True}],
    )
    monkeypatch.setattr(
        "analysis.shift_optimizer.optimize_shifts_range",
        lambda *_args, **_kwargs: {
            "days": 28,
            "summary": {"days_with_predictions": 28},
            "weekly_templates": [],
        },
    )

    context = gather_chat_context(
        "site-1",
        "What should we do for staffing efficiency over the next 2 weeks?",
    )

    assert "daily_efficiency" in context
    assert "next_actions_live" in context
    assert "recent_recommendations" in context
    assert "optimized_shift_range" in context
    assert "bottom_line_scorecard" in context


def test_gather_chat_context_does_not_load_item_variations_for_non_item_questions(monkeypatch):
    _patch_common_chat_dependencies(monkeypatch)
    called = {"item_variations": 0}

    def _count_item_variations(*_args, **_kwargs):
        called["item_variations"] += 1
        return []

    monkeypatch.setattr("app.chat._get_item_variations", _count_item_variations)
    monkeypatch.setattr("app.chat._get_recent_efficiency_context", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "analysis.next_actions.generate_next_actions", lambda *_args, **_kwargs: {"actions": []}
    )
    monkeypatch.setattr("app.chat._get_recent_recommendations", lambda *_args, **_kwargs: [])

    context = gather_chat_context("site-1", "How is profitability and labor efficiency this week?")

    assert called["item_variations"] == 0
    assert "item_variations" not in context


def test_gather_chat_context_includes_inventory_alerts_for_stock_question(monkeypatch):
    _patch_common_chat_dependencies(monkeypatch)
    monkeypatch.setattr(
        "app.chat.get_inventory_alerts",
        lambda *_args, **_kwargs: [
            {
                "item_name": "12oz cups",
                "status": "low_stock",
                "severity": "warning",
                "effective_on_hand": 120,
                "reorder_point": 250,
                "days_remaining": 1.5,
                "unit": "cups",
            }
        ],
    )
    monkeypatch.setattr(
        "app.chat.list_inventory_items",
        lambda *_args, **_kwargs: [{"item_name": "12oz cups"}],
    )
    monkeypatch.setattr(
        "app.chat.list_inventory_usage_rules",
        lambda *_args, **_kwargs: [{"trigger_item_name": "latte"}],
    )

    context = gather_chat_context("site-1", "Any low stock alerts on cups or milk?")

    assert "inventory_alerts" in context
    assert context["inventory_alerts"][0]["item_name"] == "12oz cups"
    assert "inventory_items" in context
    assert "inventory_usage_rules" in context


def test_build_system_prompt_contains_grounded_efficiency_and_actions_sections():
    context = {
        "data_freshness": "2026-02-19",
        "has_real_cogs": True,
        "xero_connected": True,
        "cogs_snapshot": {"total_items": 10, "real_items": 9, "xero_items": 8, "document_items": 1},
        "daily_efficiency": {
            "date": "2026-02-19",
            "summary": {
                "intervals_analyzed": 20,
                "total_revenue_cents": 120000,
                "deputy_labor_cost_cents": 34000,
                "labor_pct": 28.3,
                "revenue_per_labor_hour_cents": 6800,
            },
            "variance_summary": {
                "understaffed_intervals": 2,
                "overstaffed_intervals": 1,
                "no_staff_intervals": 0,
            },
            "peaks": {"mismatch": []},
        },
        "next_actions_live": {
            "summary": {
                "proven_gate": {"suppressed_count": 1, "suppressed_action_types": ["PRICE_TEST_UP"]}
            },
            "actions": [
                {
                    "title": "Add 1 staff during peak block",
                    "expected_weekly_profit_uplift_cents": 12000,
                    "proven_weekly_impact_cents": 7000,
                    "confidence": 0.77,
                    "proven_gate_status": "positive_realized_impact",
                    "realized_samples": 3,
                }
            ],
        },
        "recent_recommendations": [
            {
                "title": "Trim overstaffed block",
                "expected_weekly_profit_uplift_cents": 5000,
                "adopted": False,
            }
        ],
        "optimized_shift_range": {
            "days": 28,
            "summary": {"days_with_predictions": 28},
            "weekly_templates": [
                {
                    "day_of_week": "Mon",
                    "status": "ok",
                    "template_shifts": [1, 2],
                    "avg_estimated_labor_delta_cents": -1200,
                }
            ],
        },
        "bottom_line_scorecard": {
            "headline": "Net profit and labor efficiency are improving versus prior window.",
            "kpis": {
                "total_net_profit_cents": 150000,
                "net_margin_pct": 11.8,
                "avg_labor_pct": 30.2,
                "avg_revenue_per_labor_hour_cents": 6600,
            },
            "trend": {
                "deltas": {
                    "net_profit_cents": 12000,
                    "labor_pct_delta_pp": -0.9,
                    "revenue_per_labor_hour_delta_pct": 3.6,
                },
                "directions": {
                    "net_profit": "improving",
                    "labor_pct": "improving",
                    "revenue_per_labor_hour": "improving",
                },
            },
            "actions": {
                "recommendations_generated": 8,
                "recommendations_adopted": 5,
                "realized_actions": 4,
                "avg_realized_weekly_profit_delta_cents": 5400,
                "top_proven_action_types": [
                    {
                        "action_type": "CUT_STAFF_BLOCK",
                        "realized_count": 4,
                        "avg_realized_weekly_profit_delta_cents": 5400,
                    }
                ],
            },
            "financial_truth": {
                "mode": "xero_factual",
                "coverage_days": 28,
                "window_days": 30,
                "income_cents": 4800000,
                "expense_cents": 3100000,
                "net_cash_cents": 1700000,
            },
        },
    }

    prompt = build_system_prompt("Clubhouse", context)

    assert "COGS STATUS" in prompt
    assert "Daily Efficiency Snapshot" in prompt
    assert "Bottom-Line Scorecard (30d)" in prompt
    assert "Financial truth source" in prompt
    assert "Rule: use Square for sales breakdowns" in prompt
    assert "Proven Action Types" in prompt
    assert "Recommended Next Actions (live)" in prompt
    assert "Recent Recommendation Memory" in prompt
    assert "28-Day Shift Optimization" in prompt


def test_build_system_prompt_handles_string_xero_mapping_confidence():
    context = {
        "data_freshness": "2026-02-20",
        "xero_mappings": [
            {
                "xero_description": "Oat Milk 1L",
                "score_key": "oat_milk",
                "confidence": "medium",
                "units_per_pack": 12,
            },
            {
                "xero_description": "Coffee Beans",
                "score_key": "beans",
                "confidence": "high",
                "units_per_pack": 1,
            },
        ],
    }

    prompt = build_system_prompt("Clubhouse", context)

    assert "Xero Supplier Mappings" in prompt
    assert "2 items mapped" in prompt
