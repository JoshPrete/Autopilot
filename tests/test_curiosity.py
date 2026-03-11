from analysis.curiosity import build_curiosity_agenda


def test_build_curiosity_agenda_includes_gap_learning_and_workflow_learning():
    agenda = build_curiosity_agenda(
        "site-1",
        top_items=[{"item": "12oz coffee", "count": 84, "avg_workload": 3.2}],
        inventory_alerts=[],
        inventory_usage_patterns=[],
        operator_rules=[],
        usage_rules=[],
        bottom_line_scorecard={
            "targets": {
                "primary_lever": {"focus": "labor_efficiency"},
                "gaps": {"weekly_labor_reduction_needed_cents": 5400},
            }
        },
        limit=5,
    )

    assert agenda
    assert any(item["agenda_type"] == "knowledge_gap" for item in agenda)
    assert any(item["agenda_type"] == "workflow_learning" for item in agenda)
    workflow = next(item for item in agenda if item["agenda_type"] == "workflow_learning")
    assert "which role can flex" in workflow["question"].lower()


def test_build_curiosity_agenda_uses_inventory_patterns_without_drivers():
    agenda = build_curiosity_agenda(
        "site-1",
        top_items=[],
        inventory_alerts=[],
        inventory_usage_patterns=[
            {
                "item_name": "12oz cups",
                "total_consumed_units": 180,
                "top_usage_triggers": [],
            }
        ],
        operator_rules=[{"rule_type": "staffing_constraint"}],
        usage_rules=[],
        bottom_line_scorecard=None,
        limit=5,
    )

    assert any(item["agenda_type"] == "consumption_mapping" for item in agenda)
    mapping = next(item for item in agenda if item["agenda_type"] == "consumption_mapping")
    assert "12oz cups" in mapping["question"]
