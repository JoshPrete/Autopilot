from analysis.knowledge_gaps import detect_knowledge_gaps


def test_detect_knowledge_gaps_flags_missing_recipe_for_top_seller():
    gaps = detect_knowledge_gaps(
        "site-1",
        lookback_days=30,
        top_items=[{"item": "12oz coffee", "count": 84, "avg_workload": 3.2}],
        inventory_alerts=[],
        operator_rules=[],
        usage_rules=[],
    )

    assert gaps
    assert gaps[0]["gap_type"] == "missing_recipe"
    assert "12oz coffee" in gaps[0]["question"]


def test_detect_knowledge_gaps_flags_missing_schedule_before_purchase_profile():
    gaps = detect_knowledge_gaps(
        "site-1",
        lookback_days=30,
        top_items=[],
        inventory_alerts=[
            {
                "item_name": "oat milk",
                "status": "stockout_before_delivery",
                "daily_usage_units": 6.5,
                "unit": "L",
                "recommended_reorder_units": 24,
                "schedule_source": None,
                "order_profile_source": None,
            }
        ],
        operator_rules=[],
        usage_rules=[],
    )

    assert len(gaps) >= 2
    assert gaps[0]["gap_type"] == "missing_delivery_schedule"
    assert gaps[1]["gap_type"] == "missing_purchase_profile"
    assert "oat milk" in gaps[0]["title"]
