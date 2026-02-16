from datetime import date

from analysis.profitability import compute_item_margins
from data.deputy import DeputyClient
from data.storage import get_daily_profitability


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


class TestDeputyDateBounds:
    def test_fetch_rosters_uses_exclusive_next_day_end_bound(self):
        client = DeputyClient(base_url="https://example.deputy.com", access_token="token")
        captured = {}

        def _fake_request(method, endpoint, **kwargs):
            captured["method"] = method
            captured["endpoint"] = endpoint
            captured["payload"] = kwargs["json"]
            return []

        client._request = _fake_request

        client.fetch_rosters(date(2026, 2, 12), date(2026, 2, 12))

        assert captured["method"] == "POST"
        assert captured["endpoint"] == "resource/Roster/QUERY"
        assert captured["payload"]["search"]["s1"]["data"] == "2026-02-12"
        assert captured["payload"]["search"]["s2"]["type"] == "lt"
        assert captured["payload"]["search"]["s2"]["data"] == "2026-02-13"


class TestMarginQuantityParsing:
    def test_compute_item_margins_accepts_decimal_string_quantity(self, monkeypatch):
        payload = {
            "line_items": [
                {
                    "name": "Latte",
                    "quantity": "1.000000",
                    "total_money": {"amount": 550},
                }
            ]
        }
        monkeypatch.setattr(
            "analysis.profitability.engine",
            _DummyEngine(_DummyAllResult([(payload,)])),
        )
        monkeypatch.setattr("analysis.profitability.seed_item_costs", lambda _sid: 0)
        monkeypatch.setattr("analysis.profitability.get_item_costs", lambda _sid: {"latte": 140})

        result = compute_item_margins("site-1", days=14)

        assert len(result) == 1
        assert result[0]["qty"] == 1
        assert result[0]["avg_price_cents"] == 550


class TestDailyProfitabilityRowParsing:
    def test_get_daily_profitability_preserves_zero_values(self, monkeypatch):
        rows = [
            (
                date(2026, 2, 12),
                1000,  # revenue_cents
                0,  # labor_cost_cents
                0,  # cogs_cents
                0,  # gross_profit_cents
                0,  # net_profit_cents
                0,  # order_count
                0,  # item_count
                0,  # drink_count
                0.0,  # labor_hours
                0,  # revenue_per_labor_hour
                0,  # cost_per_drink
                0.0,  # labor_pct
            )
        ]
        monkeypatch.setattr("data.storage.engine", _DummyEngine(rows))

        result = get_daily_profitability("site-1", date(2026, 2, 12), date(2026, 2, 12))

        assert len(result) == 1
        day = result[0]
        assert day["labor_cost_cents"] == 0
        assert day["cogs_cents"] == 0
        assert day["gross_profit_cents"] == 0
        assert day["net_profit_cents"] == 0
        assert day["order_count"] == 0
        assert day["item_count"] == 0
        assert day["drink_count"] == 0
        assert day["labor_hours"] == 0.0
        assert day["revenue_per_labor_hour"] == 0
        assert day["cost_per_drink"] == 0
        assert day["labor_pct"] == 0.0
