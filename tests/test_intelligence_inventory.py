from datetime import date

from analysis.intelligence import detect_inventory_signals


def test_detect_inventory_signals_builds_restock_actions(monkeypatch):
    monkeypatch.setattr(
        "data.storage.get_inventory_alerts",
        lambda *_args, **_kwargs: [
            {
                "item_name": "12oz cups",
                "status": "low_stock",
                "effective_on_hand": 120,
                "reorder_point": 250,
                "recommended_reorder_units": 880,
            },
            {
                "item_name": "oat milk",
                "status": "reorder_soon",
                "effective_on_hand": 9,
                "reorder_point": 6,
                "days_remaining": 1.8,
                "recommended_reorder_units": 15,
            },
            {
                "item_name": "soy milk",
                "status": "needs_count",
            },
        ],
    )

    signals = detect_inventory_signals("site-1", date(2026, 2, 21), lookback_days=21)

    assert len(signals) == 3
    assert all(s["suggested_action"] == "INVENTORY_RESTOCK" for s in signals)
    by_title = {s["title"]: s for s in signals}
    assert any("12oz cups" in title for title in by_title)
    assert any("oat milk" in title for title in by_title)
    assert any("needs a physical stock count" in title for title in by_title)
    assert any(s["severity"] == "warning" for s in signals)
    assert any(s["severity"] == "opportunity" for s in signals)
