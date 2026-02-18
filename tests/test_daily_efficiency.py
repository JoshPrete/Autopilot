from datetime import date, datetime

from data.storage import get_daily_efficiency_snapshot


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

    def first(self):
        return self._rows[0] if self._rows else None

    def __iter__(self):
        return iter(self._rows)


class _DummyConn:
    def __init__(self, result_sets):
        self._result_sets = result_sets
        self._idx = 0

    def execute(self, *_args, **_kwargs):
        rows = self._result_sets[self._idx]
        self._idx += 1
        return _DummyResult(rows)


class _DummyEngine:
    def __init__(self, result_sets):
        self._result_sets = result_sets

    def connect(self):
        return _DummyConnectCtx(_DummyConn(self._result_sets))


def test_daily_efficiency_snapshot(monkeypatch):
    variance = {
        "summary": {
            "understaffed_intervals": 1,
            "overstaffed_intervals": 1,
            "balanced_intervals": 0,
            "no_staff_intervals": 0,
        },
        "intervals": [
            {
                "interval_start": "2026-02-18T08:00:00",
                "workload_units": 9.5,
                "items_count": 18,
                "staff_on": 2,
                "expected_staff": 3,
                "staff_delta": -1,
                "workload_per_staff": 4.75,
                "status": "understaffed",
                "severity": "high",
            },
            {
                "interval_start": "2026-02-18T08:15:00",
                "workload_units": 3.0,
                "items_count": 6,
                "staff_on": 3,
                "expected_staff": 1,
                "staff_delta": 2,
                "workload_per_staff": 1.0,
                "status": "overstaffed",
                "severity": "medium",
            },
        ],
    }

    trade_rows = [
        {"interval_start": datetime(2026, 2, 18, 8, 0), "orders_count": 7, "revenue_cents": 15600},
        {"interval_start": datetime(2026, 2, 18, 8, 15), "orders_count": 2, "revenue_cents": 4200},
    ]
    deputy_totals = [{"shifts_count": 5, "total_hours": 8.0, "total_cost_dollars": 240.0}]

    monkeypatch.setattr("data.storage.get_staffing_variance_intervals", lambda *_args, **_kwargs: variance)
    monkeypatch.setattr("data.storage.engine", _DummyEngine([trade_rows, deputy_totals]))

    result = get_daily_efficiency_snapshot("site-1", date(2026, 2, 18))

    assert result["summary"]["total_revenue_cents"] == 19800
    assert result["summary"]["total_orders"] == 9
    assert result["summary"]["deputy_staff_hours"] == 8.0
    assert result["summary"]["deputy_labor_cost_cents"] == 24000
    assert result["summary"]["labor_pct"] == 121.21
    assert result["summary"]["revenue_per_labor_hour_cents"] == 2475

    assert result["peaks"]["trade"][0]["interval_start"] == "2026-02-18T08:00:00"
    assert result["peaks"]["trade"][0]["revenue_per_staff_hour_cents"] == 31200
    assert result["peaks"]["mismatch"][0]["status"] in ("understaffed", "overstaffed")
