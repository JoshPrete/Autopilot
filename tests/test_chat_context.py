from app.chat import build_system_prompt, gather_chat_context


def _patch_common_chat_dependencies(monkeypatch):
    monkeypatch.setattr("app.chat.get_data_freshness", lambda *_args, **_kwargs: "2026-02-19")
    monkeypatch.setattr(
        "app.chat.get_data_health",
        lambda *_args, **_kwargs: {
            "status": "green",
            "score": 1.0,
            "components": [
                {
                    "source": "square_orders",
                    "status": "green",
                    "latest_date": "2026-02-19",
                    "age_days": 0,
                },
                {
                    "source": "daily_profitability",
                    "status": "green",
                    "latest_date": "2026-02-19",
                    "age_days": 0,
                },
            ],
        },
    )
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
    monkeypatch.setattr("app.chat.get_inventory_usage_patterns", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.list_inventory_items", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.list_inventory_usage_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.list_operator_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.detect_knowledge_gaps", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("app.chat.build_curiosity_agenda", lambda *_args, **_kwargs: [])
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
        "app.chat.get_inventory_usage_patterns",
        lambda *_args, **_kwargs: [
            {
                "item_name": "12oz cups",
                "total_consumed_units": 180,
                "avg_daily_consumed_units": 8.6,
                "unit": "cups",
                "lookback_days": 21,
                "top_usage_triggers": [
                    {
                        "trigger_item_name": "12oz latte",
                        "share_pct": 62.0,
                    }
                ],
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
    assert "inventory_usage_patterns" in context
    assert (
        context["inventory_usage_patterns"][0]["top_usage_triggers"][0]["trigger_item_name"]
        == "12oz latte"
    )
    assert "inventory_items" in context
    assert "inventory_usage_rules" in context


def test_gather_chat_context_builds_direct_lookup_for_whos_working_tomorrow(monkeypatch):
    _patch_common_chat_dependencies(monkeypatch)
    monkeypatch.setattr("app.chat._today_local", lambda: __import__("datetime").date(2026, 2, 19))
    monkeypatch.setattr(
        "app.chat.get_data_health",
        lambda *_args, **_kwargs: {
            "status": "green",
            "score": 1.0,
            "components": [
                {
                    "source": "deputy_rosters",
                    "status": "green",
                    "latest_date": "2026-02-20",
                    "next_14d_shifts": 14,
                }
            ],
        },
    )

    def _roster(_site_id, target_date):
        if str(target_date) == "2026-02-20":
            return [
                {
                    "name": "Sarah",
                    "start": "06:30",
                    "end": "12:30",
                    "hours": 6.0,
                    "is_open": False,
                },
                {
                    "name": "Tom",
                    "start": "07:00",
                    "end": "13:00",
                    "hours": 6.0,
                    "is_open": False,
                },
            ]
        return []

    monkeypatch.setattr("app.chat._get_roster_for_date", _roster)

    context = gather_chat_context("site-1", "Who's working tomorrow?")

    assert context["request_plan"]["intent"] == "direct_lookup"
    assert context["request_plan"]["lookup_key"] == "tomorrow_roster"
    assert context["direct_lookup"]["source_of_truth"] == "deputy_rosters"
    assert context["direct_lookup"]["status"] == "ok"
    assert len(context["direct_lookup"]["shifts"]) == 2
    assert context["question_source_basis"][0]["source"] == "deputy_rosters"


def test_gather_chat_context_refreshes_deputy_for_tomorrow_lookup_when_future_roster_missing(
    monkeypatch,
):
    _patch_common_chat_dependencies(monkeypatch)
    monkeypatch.setattr("app.chat._today_local", lambda: __import__("datetime").date(2026, 2, 19))

    health_calls = {"count": 0}

    def _health(*_args, **_kwargs):
        health_calls["count"] += 1
        if health_calls["count"] == 1:
            return {
                "status": "yellow",
                "score": 0.5,
                "components": [
                    {
                        "source": "deputy_rosters",
                        "status": "yellow",
                        "latest_date": "2026-02-19",
                        "next_14d_shifts": 0,
                    }
                ],
            }
        return {
            "status": "green",
            "score": 1.0,
            "components": [
                {
                    "source": "deputy_rosters",
                    "status": "green",
                    "latest_date": "2026-02-20",
                    "next_14d_shifts": 12,
                }
            ],
        }

    monkeypatch.setattr("app.chat.get_data_health", _health)

    roster_state = {"refreshed": False}

    def _roster(_site_id, target_date):
        if str(target_date) != "2026-02-20":
            return []
        if not roster_state["refreshed"]:
            return []
        return [
            {
                "name": "Sarah",
                "start": "06:30",
                "end": "12:30",
                "hours": 6.0,
                "is_open": False,
            }
        ]

    def _refresh(_site_id, run_date):
        assert str(run_date) == "2026-02-19"
        roster_state["refreshed"] = True
        return {"status": "ok", "rosters": 8, "stored": 8}

    monkeypatch.setattr("app.chat._get_roster_for_date", _roster)
    monkeypatch.setattr("app.chat._refresh_deputy_rosters", _refresh)

    context = gather_chat_context("site-1", "Who's working tomorrow?")

    assert context["direct_lookup"]["status"] == "ok"
    assert context["direct_lookup"]["refresh_attempted"] is True
    assert context["direct_lookup"]["refresh_status"] == "ok"
    assert len(context["direct_lookup"]["shifts"]) == 1
    assert context["question_source_basis"][0]["status"] == "green"


def test_gather_chat_context_includes_confirmed_operator_rules(monkeypatch):
    _patch_common_chat_dependencies(monkeypatch)
    monkeypatch.setattr(
        "app.chat.list_operator_rules",
        lambda *_args, **_kwargs: [
            {
                "rule_type": "delivery_schedule",
                "rule_name": "Milk delivery schedule",
                "payload": {"subject": "Milk", "days": ["monday", "wednesday", "friday"]},
                "status": "confirmed",
            }
        ],
    )

    context = gather_chat_context("site-1", "What business rules do you know?")

    assert "operator_rules" in context
    assert context["operator_rules"][0]["rule_type"] == "delivery_schedule"


def test_gather_chat_context_includes_knowledge_gaps(monkeypatch):
    _patch_common_chat_dependencies(monkeypatch)
    monkeypatch.setattr(
        "app.chat.detect_knowledge_gaps",
        lambda *_args, **_kwargs: [
            {
                "gap_type": "missing_recipe",
                "priority": "high",
                "title": "Missing stock recipe for 12oz coffee",
                "question": "What does 12oz coffee consume from stock?",
                "why_it_matters": "12oz coffee is selling but has no stock rule.",
            }
        ],
    )

    context = gather_chat_context("site-1", "What logic am I missing?")

    assert "knowledge_gaps" in context
    assert context["knowledge_gaps"][0]["gap_type"] == "missing_recipe"


def test_gather_chat_context_includes_curiosity_agenda(monkeypatch):
    _patch_common_chat_dependencies(monkeypatch)
    monkeypatch.setattr(
        "app.chat.build_curiosity_agenda",
        lambda *_args, **_kwargs: [
            {
                "agenda_type": "workflow_learning",
                "priority": "high",
                "title": "Missing workflow staffing rules",
                "question": "Which role can flex off first during quiet periods?",
                "why_it_matters": "Labor is above target but workflow constraints are missing.",
                "decision_unlocked": "Sharper roster recommendations",
            }
        ],
    )

    context = gather_chat_context("site-1", "How can we improve profitability?")

    assert "curiosity_agenda" in context
    assert context["curiosity_agenda"][0]["agenda_type"] == "workflow_learning"


def test_gather_chat_context_includes_data_health(monkeypatch):
    _patch_common_chat_dependencies(monkeypatch)

    context = gather_chat_context("site-1", "How is trade today?")

    assert "data_health" in context
    assert context["data_health"]["status"] == "green"
    assert context["data_health"]["components"][0]["source"] == "square_orders"
    assert "question_source_basis" in context
    assert context["question_source_basis"][0]["source"] == "square_orders"


def test_build_system_prompt_contains_grounded_efficiency_and_actions_sections():
    context = {
        "data_freshness": "2026-02-19",
        "data_health": {
            "status": "yellow",
            "score": 0.67,
            "components": [
                {
                    "source": "square_orders",
                    "status": "yellow",
                    "latest_date": "2026-02-18",
                    "age_days": 1,
                },
                {
                    "source": "daily_profitability",
                    "status": "green",
                    "latest_date": "2026-02-19",
                    "age_days": 0,
                },
            ],
        },
        "question_source_basis": [
            {
                "source": "square_orders",
                "label": "Square orders",
                "status": "yellow",
                "latest_date": "2026-02-18",
                "age_days": 1,
            },
            {
                "source": "daily_profitability",
                "label": "Daily profitability",
                "status": "green",
                "latest_date": "2026-02-19",
                "age_days": 0,
            },
        ],
        "operator_rules": [
            {
                "rule_type": "delivery_schedule",
                "payload": {"subject": "Milk", "days": ["monday", "wednesday", "friday"]},
            }
        ],
        "knowledge_gaps": [
            {
                "priority": "high",
                "title": "Missing delivery schedule for oat milk",
                "question": "What days do you order or receive oat milk?",
                "why_it_matters": "Oat milk is actively consumed but has no delivery schedule.",
            }
        ],
        "curiosity_agenda": [
            {
                "priority": "high",
                "title": "Missing workflow staffing rules",
                "question": "Which role can flex off first during quiet periods?",
                "why_it_matters": "Labor is above target but workflow constraints are missing.",
                "decision_unlocked": "Sharper roster recommendations",
            }
        ],
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
                "optimization_phase": "labor_efficiency",
                "phase_reason": "Labor % remains above target.",
                "profitability_goal": {
                    "focus": "labor_efficiency",
                    "reason": "Labor % is above target while COGS is within range.",
                },
                "profitability_gaps": {
                    "weekly_labor_reduction_needed_cents": 5400,
                    "weekly_cogs_reduction_needed_cents": 0,
                    "weekly_prime_cost_reduction_needed_cents": 0,
                    "weekly_revenue_needed_for_net_margin_target_cents": 0,
                },
                "proven_gate": {
                    "suppressed_count": 1,
                    "suppressed_action_types": ["PRICE_TEST_UP"],
                },
            },
            "actions": [
                {
                    "title": "Add 1 staff during peak block",
                    "expected_weekly_profit_uplift_cents": 12000,
                    "proven_weekly_impact_cents": 7000,
                    "confidence": 0.77,
                    "proven_gate_status": "positive_realized_impact",
                    "realized_samples": 3,
                    "profitability_alignment": {
                        "reason": "Protects profitability by avoiding service loss in peak intervals while labor remains the primary constraint."
                    },
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
            "summary": {
                "days_with_predictions": 28,
                "profitability_context": {
                    "primary_lever": {
                        "focus": "labor_efficiency",
                        "reason": "Labor % is above target while COGS is within range.",
                    },
                    "gaps": {"weekly_labor_reduction_needed_cents": 5400},
                    "estimated_weekly_labor_savings_cents": 4200,
                },
            },
            "weekly_templates": [
                {
                    "day_of_week": "Mon",
                    "status": "ok",
                    "template_shifts": [1, 2],
                    "avg_estimated_labor_delta_cents": -1200,
                    "profitability_alignment": {
                        "note": "Contributes about $12 toward the weekly labor reduction target of $54."
                    },
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
            "targets": {
                "targets": {
                    "labor_pct_high": 28.0,
                    "cogs_pct_high": 35.0,
                    "prime_cost_pct_high": 62.0,
                },
                "current": {
                    "labor_pct": 30.2,
                    "cogs_pct": 27.4,
                    "prime_cost_pct": 57.6,
                },
                "gaps": {
                    "weekly_labor_reduction_needed_cents": 5400,
                    "weekly_cogs_reduction_needed_cents": 0,
                    "weekly_prime_cost_reduction_needed_cents": 0,
                    "weekly_revenue_needed_for_prime_target_cents": 0,
                },
                "primary_lever": {
                    "focus": "labor_efficiency",
                    "reason": "Labor % is above target while COGS is within range.",
                },
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

    assert "Confirmed Operating Rules" in prompt
    assert "Data Health" in prompt
    assert "Question Source Basis" in prompt
    assert "square_orders: YELLOW, latest 18/02/2026, 1 days old" in prompt
    assert "Square orders: YELLOW, latest 18/02/2026, 1 days old" in prompt
    assert "Milk: delivery on Monday, Wednesday, Friday" in prompt
    assert "High-Priority Knowledge Gaps" in prompt
    assert "What days do you order or receive oat milk?" in prompt
    assert "Curiosity Agenda" in prompt
    assert "Which role can flex off first during quiet periods?" in prompt
    assert "COGS STATUS" in prompt
    assert "Daily Efficiency Snapshot" in prompt
    assert "Bottom-Line Scorecard (30d)" in prompt
    assert "Financial truth source" in prompt
    assert "Margin Target Gap" in prompt
    assert "Primary lever: labor_efficiency" in prompt
    assert "Rule: use Square for sales breakdowns" in prompt
    assert "Proven Action Types" in prompt
    assert "Recommended Next Actions (live)" in prompt
    assert "Profitability focus: labor_efficiency" in prompt
    assert "Active weekly gaps: labor $54" in prompt
    assert "Protects profitability by avoiding service loss in peak intervals" in prompt
    assert "Recent Recommendation Memory" in prompt
    assert "28-Day Shift Optimization" in prompt
    assert "Estimated weekly roster labor savings: $42 against target $54" in prompt
    assert (
        "Profitability: Contributes about $12 toward the weekly labor reduction target of $54."
        in prompt
    )


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


def test_build_system_prompt_contains_direct_lookup_guidance():
    prompt = build_system_prompt(
        "Clubhouse",
        {
            "today_date": "2026-02-19",
            "request_plan": {
                "intent": "direct_lookup",
                "lookup_key": "tomorrow_roster",
                "label": "Tomorrow roster lookup",
                "sources": ["deputy_rosters"],
            },
            "direct_lookup": {
                "lookup_key": "tomorrow_roster",
                "label": "Tomorrow roster",
                "source_of_truth": "deputy_rosters",
                "date": "2026-02-20",
                "status": "ok",
                "shifts": [
                    {"name": "Sarah", "start": "06:30", "end": "12:30", "hours": 6.0},
                    {"name": "Tom", "start": "07:00", "end": "13:00", "hours": 6.0},
                ],
            },
            "question_source_basis": [
                {
                    "label": "Deputy rosters",
                    "status": "green",
                    "latest_date": "2026-02-20",
                    "age_days": 0,
                }
            ],
        },
    )

    assert "## Request Plan" in prompt
    assert "Intent: DIRECT_LOOKUP" in prompt
    assert "This is a direct factual lookup." in prompt
    assert "## Direct Lookup" in prompt
    assert "Source of truth: deputy_rosters" in prompt
    assert "Sarah: 06:30 – 12:30 (6.0h)" in prompt
