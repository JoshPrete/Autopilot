from datetime import datetime

from data.storage import (
    _build_virtual_inventory_usage_rules,
    _resolve_inventory_schedule_context,
    get_inventory_alerts,
)


class _FixedDatetime(datetime):
    @classmethod
    def utcnow(cls):
        return cls(2026, 2, 17, 9, 0, 0)


class _DummyConnectCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)

    def scalar(self):
        if not self._rows:
            return None
        first = self._rows[0]
        if isinstance(first, dict) and len(first) == 1:
            return next(iter(first.values()))
        return first


class _DummyConn:
    def __init__(self, engine):
        self._engine = engine

    def execute(self, *_args, **_kwargs):
        rows = self._engine._result_sets[self._engine._idx]
        self._engine._idx += 1
        return _DummyResult(rows)


class _DummyEngine:
    def __init__(self, result_sets):
        self._result_sets = result_sets
        self._idx = 0

    def connect(self):
        return _DummyConnectCtx(_DummyConn(self))


def test_build_virtual_inventory_usage_rules_from_recipe_definition():
    items = [
        {"inventory_item_id": "inv-cup", "item_name": "12oz cups", "unit": "each"},
        {"inventory_item_id": "inv-beans", "item_name": "coffee beans", "unit": "g"},
    ]
    operator_rules = [
        {
            "rule_id": "rule-1",
            "rule_type": "recipe_definition",
            "payload": {
                "trigger_item_name": "12oz latte",
                "components": [
                    {"item_name": "12oz cups", "quantity": 1, "unit": "each"},
                    {"item_name": "coffee beans", "quantity": 20, "unit": "g"},
                ],
            },
            "updated_at": "2026-02-17T09:00:00",
        }
    ]

    rules = _build_virtual_inventory_usage_rules(items, [], operator_rules)

    assert len(rules) == 2
    assert {rule["inventory_item_id"] for rule in rules} == {"inv-cup", "inv-beans"}
    assert all(rule["source"] == "recipe_definition" for rule in rules)
    assert rules[0]["trigger_item_name"] == "12oz latte"


def test_resolve_inventory_schedule_context_flags_stockout_before_delivery():
    item = {
        "item_name": "oat milk",
        "score_key": "oat_milk_ml",
        "lead_time_days": 2,
    }
    operator_rules = [
        {
            "rule_type": "ordering_schedule",
            "payload": {
                "subject": "oat milk",
                "cutoff_day": "tuesday",
                "cutoff_time": "14:00",
                "delivery_day": "wednesday",
            },
        }
    ]

    context = _resolve_inventory_schedule_context(
        item=item,
        operator_rules=operator_rules,
        as_of=datetime(2026, 2, 17, 9, 0, 0),
        effective_on_hand=2.0,
        daily_usage_units=3.0,
    )

    assert context["schedule_source"] == "ordering_schedule"
    assert context["next_delivery_date"] == "2026-02-18"
    assert context["stockout_before_next_delivery"] is True
    assert context["order_timing_status"] == "order_now"


def test_get_inventory_alerts_applies_recipe_rules_and_schedule_context(monkeypatch):
    monkeypatch.setattr("data.storage.datetime", _FixedDatetime)
    monkeypatch.setattr("data.storage._get_xero_pack_profiles", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "data.storage.list_inventory_items",
        lambda *_args, **_kwargs: [
            {
                "inventory_item_id": "inv-cup",
                "item_name": "12oz cups",
                "score_key": "cup_12oz",
                "unit": "each",
                "reorder_point": 200,
                "par_level": 1000,
                "lead_time_days": 2,
                "last_count_on_hand": 600,
                "last_counted_at": "2026-02-15T09:00:00",
            }
        ],
    )
    monkeypatch.setattr("data.storage.list_inventory_usage_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(
        "data.storage.list_operator_rules",
        lambda *_args, **_kwargs: [
            {
                "rule_id": "rule-recipe",
                "rule_type": "recipe_definition",
                "status": "confirmed",
                "payload": {
                    "trigger_item_name": "12oz latte",
                    "components": [{"item_name": "12oz cups", "quantity": 1, "unit": "each"}],
                },
                "updated_at": "2026-02-17T09:00:00",
            },
            {
                "rule_id": "rule-order",
                "rule_type": "ordering_schedule",
                "status": "confirmed",
                "payload": {
                    "subject": "12oz cups",
                    "cutoff_day": "tuesday",
                    "cutoff_time": "14:00",
                    "delivery_day": "wednesday",
                },
                "updated_at": "2026-02-17T09:00:00",
            },
        ],
    )
    monkeypatch.setattr(
        "data.storage.engine",
        _DummyEngine(
            [
                [],
                [
                    {
                        "item_name": "12oz latte",
                        "quantity": 500,
                        "modifiers": "",
                        "created_at": datetime(2026, 2, 16, 8, 0, 0),
                    }
                ],
            ]
        ),
    )

    alerts = get_inventory_alerts("site-1", lookback_days=21, include_ok=False)

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["item_name"] == "12oz cups"
    assert alert["status"] == "stockout_before_delivery"
    assert alert["consumed_since_count"] == 500.0
    assert alert["usage_rule_sources"] == ["recipe_definition"]
    assert alert["next_delivery_date"] == "2026-02-18"
    assert alert["order_timing_status"] == "order_now"
    assert alert["stockout_before_next_delivery"] is True
    assert alert["projected_on_hand_at_next_delivery"] < 0
    assert alert["recommended_reorder_units"] > 1000


def test_get_inventory_alerts_converts_shortfall_into_order_units(monkeypatch):
    monkeypatch.setattr("data.storage.datetime", _FixedDatetime)
    monkeypatch.setattr("data.storage._get_xero_pack_profiles", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        "data.storage.list_inventory_items",
        lambda *_args, **_kwargs: [
            {
                "inventory_item_id": "inv-cup",
                "item_name": "12oz cups",
                "score_key": "cup_12oz",
                "unit": "each",
                "reorder_point": 300,
                "par_level": 1000,
                "lead_time_days": 2,
                "last_count_on_hand": 250,
                "last_counted_at": "2026-02-15T09:00:00",
                "metadata": {
                    "units_per_order": 1000,
                    "order_unit_name": "case",
                    "minimum_order_units": 2,
                    "order_multiple_units": 2,
                    "supplier_name": "Bidfood",
                },
            }
        ],
    )
    monkeypatch.setattr("data.storage.list_inventory_usage_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("data.storage.list_operator_rules", lambda *_args, **_kwargs: [])
    monkeypatch.setattr("data.storage.engine", _DummyEngine([[]]))

    alerts = get_inventory_alerts("site-1", lookback_days=21, include_ok=False)

    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["status"] == "low_stock"
    assert alert["recommended_reorder_units"] == 750.0
    assert alert["recommended_order_count"] == 2
    assert alert["recommended_order_quantity_units"] == 2000.0
    assert alert["order_unit_name"] == "case"
    assert alert["order_profile_source"] == "metadata"
    assert "Bidfood" in alert["recommended_order_note"]
