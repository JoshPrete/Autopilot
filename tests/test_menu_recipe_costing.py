from analysis.profitability import compute_item_margins


class _DummyConnectCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyConn:
    def __init__(self, execute_result):
        self._execute_result = execute_result

    def execute(self, *_args, **_kwargs):
        return self._execute_result


class _DummyEngine:
    def __init__(self, execute_result):
        self._execute_result = execute_result

    def connect(self):
        return _DummyConnectCtx(_DummyConn(self._execute_result))


class _DummyAllResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


_INVENTORY_ITEMS = [
    {"inventory_item_id": "cup", "item_name": "12oz cups", "score_key": "cup_12oz", "unit": "each"},
    {
        "inventory_item_id": "lid",
        "item_name": "90mm lids",
        "score_key": "lid_90mm",
        "unit": "each",
    },
    {
        "inventory_item_id": "beans",
        "item_name": "coffee beans",
        "score_key": "coffee_beans_g",
        "unit": "g",
    },
    {
        "inventory_item_id": "milk",
        "item_name": "full cream milk",
        "score_key": "full_cream_milk_ml",
        "unit": "ml",
    },
    {
        "inventory_item_id": "oat",
        "item_name": "oat milk",
        "score_key": "oat_milk_ml",
        "unit": "ml",
    },
]

_LATTE_USAGE_RULES = [
    {
        "rule_id": "cup-rule",
        "inventory_item_id": "cup",
        "inventory_item_name": "12oz cups",
        "inventory_unit": "each",
        "trigger_item_name": "latte",
        "required_modifier_terms": None,
        "excluded_modifier_terms": None,
        "units_per_sale": 1,
        "priority": 10,
    },
    {
        "rule_id": "lid-rule",
        "inventory_item_id": "lid",
        "inventory_item_name": "90mm lids",
        "inventory_unit": "each",
        "trigger_item_name": "latte",
        "required_modifier_terms": None,
        "excluded_modifier_terms": None,
        "units_per_sale": 1,
        "priority": 10,
    },
    {
        "rule_id": "beans-rule",
        "inventory_item_id": "beans",
        "inventory_item_name": "coffee beans",
        "inventory_unit": "g",
        "trigger_item_name": "latte",
        "required_modifier_terms": None,
        "excluded_modifier_terms": None,
        "units_per_sale": 20,
        "priority": 20,
    },
    {
        "rule_id": "milk-rule",
        "inventory_item_id": "milk",
        "inventory_item_name": "full cream milk",
        "inventory_unit": "ml",
        "trigger_item_name": "latte",
        "required_modifier_terms": None,
        "excluded_modifier_terms": "oat,soy,almond,skim",
        "units_per_sale": 355,
        "priority": 30,
    },
    {
        "rule_id": "oat-rule",
        "inventory_item_id": "oat",
        "inventory_item_name": "oat milk",
        "inventory_unit": "ml",
        "trigger_item_name": "latte",
        "required_modifier_terms": "oat",
        "excluded_modifier_terms": None,
        "units_per_sale": 355,
        "priority": 31,
    },
]

_COSTS_DETAILED = [
    {
        "score_key": "coffee_beans_1kg",
        "category": "ingredient",
        "cost_cents": 2500,
        "description": "Coffee beans 1kg",
        "source": "xero",
    },
    {
        "score_key": "oat_milk",
        "category": "ingredient",
        "cost_cents": 263,
        "description": "Oat milk 1L",
        "source": "xero",
    },
    {
        "score_key": "eco_cup_16oz",
        "category": "packaging",
        "cost_cents": 8,
        "description": "Takeaway cup",
        "source": "xero",
    },
    {
        "score_key": "cup_lid_travel",
        "category": "packaging",
        "cost_cents": 3,
        "description": "Travel lid",
        "source": "xero",
    },
    {
        "score_key": "latte",
        "category": "drink",
        "cost_cents": 140,
        "description": "Fallback latte cost",
        "source": "default",
    },
    {
        "score_key": "iced_latte",
        "category": "drink",
        "cost_cents": 160,
        "description": "Fallback iced latte cost",
        "source": "default",
    },
]


def _patch_recipe_context(monkeypatch):
    monkeypatch.setattr("analysis.profitability.seed_item_costs", lambda _sid: 0)
    monkeypatch.setattr(
        "analysis.profitability.get_item_costs",
        lambda _sid: {"latte": 140, "iced_latte": 160},
    )
    monkeypatch.setattr("analysis.profitability.list_inventory_items", lambda *_a, **_k: _INVENTORY_ITEMS)
    monkeypatch.setattr(
        "analysis.profitability.list_inventory_usage_rules",
        lambda *_a, **_k: _LATTE_USAGE_RULES,
    )
    monkeypatch.setattr("analysis.profitability.list_operator_rules", lambda *_a, **_k: [])
    monkeypatch.setattr(
        "analysis.profitability.get_item_costs_detailed",
        lambda *_a, **_k: _COSTS_DETAILED,
    )


def test_menu_costing_uses_recipe_for_oat_latte(monkeypatch):
    payload = {
        "line_items": [
            {
                "name": "12oz/Medium",
                "quantity": "1.000000",
                "total_money": {"amount": 550},
                "modifiers": [{"name": "Oat Milk"}],
            }
        ]
    }
    monkeypatch.setattr(
        "analysis.profitability.engine",
        _DummyEngine(_DummyAllResult([(payload,)])),
    )
    _patch_recipe_context(monkeypatch)

    result = compute_item_margins("site-1", days=14)

    assert len(result) == 1
    assert result[0]["score_key"] == "latte"
    assert result[0]["cogs_source"] == "recipe"
    assert result[0]["cogs_cents"] == 154
    assert "oat milk" in result[0]["cogs_detail"].lower()


def test_menu_costing_marks_estimated_when_full_cream_cost_missing(monkeypatch):
    payload = {
        "line_items": [
            {
                "name": "12oz/Medium",
                "quantity": "1.000000",
                "total_money": {"amount": 550},
                "modifiers": [],
            }
        ]
    }
    monkeypatch.setattr(
        "analysis.profitability.engine",
        _DummyEngine(_DummyAllResult([(payload,)])),
    )
    _patch_recipe_context(monkeypatch)

    result = compute_item_margins("site-1", days=14)

    assert len(result) == 1
    assert result[0]["cogs_source"] == "recipe_estimate"
    assert result[0]["cogs_cents"] == 139
    assert "fallback estimates" in result[0]["cogs_detail"].lower()


def test_menu_costing_falls_back_when_recipe_is_missing(monkeypatch):
    payload = {
        "line_items": [
            {
                "name": "12oz/ICED SMALL",
                "quantity": "1.000000",
                "total_money": {"amount": 650},
                "modifiers": [],
            }
        ]
    }
    monkeypatch.setattr(
        "analysis.profitability.engine",
        _DummyEngine(_DummyAllResult([(payload,)])),
    )
    _patch_recipe_context(monkeypatch)

    result = compute_item_margins("site-1", days=14)

    assert len(result) == 1
    assert result[0]["score_key"] == "iced_latte"
    assert result[0]["cogs_source"] == "default_flat"
    assert result[0]["cogs_cents"] == 160
