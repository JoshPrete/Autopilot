from datetime import date

from data.storage import get_staffing_variance_intervals


class _DummyConnectCtx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _DummyConn:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return _DummyResult(self._rows)


class _DummyEngine:
    def __init__(self, rows):
        self._rows = rows

    def connect(self):
        return _DummyConnectCtx(_DummyConn(self._rows))


class _DummyResult:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def __iter__(self):
        return iter(self._rows)


class TestStaffingVarianceIntervals:
    def test_classifies_under_over_and_balanced(self, monkeypatch):
        rows = [
            {
                "interval_start": "2026-02-17T08:00:00",
                "workload_units": 9.0,
                "items_count": 12,
                "staff_on": 2,
            },
            {
                "interval_start": "2026-02-17T08:15:00",
                "workload_units": 2.0,
                "items_count": 3,
                "staff_on": 2,
            },
            {
                "interval_start": "2026-02-17T08:30:00",
                "workload_units": 6.0,
                "items_count": 9,
                "staff_on": 2,
            },
            {
                "interval_start": "2026-02-17T08:45:00",
                "workload_units": 4.0,
                "items_count": 5,
                "staff_on": 0,
            },
        ]
        monkeypatch.setattr("data.storage.engine", _DummyEngine(rows))

        result = get_staffing_variance_intervals("site-1", date(2026, 2, 17))

        assert len(result["intervals"]) == 4
        assert result["intervals"][0]["status"] == "understaffed"
        assert result["intervals"][1]["status"] == "overstaffed"
        assert result["intervals"][2]["status"] == "balanced"
        assert result["intervals"][3]["status"] == "no_staff"
        assert result["summary"]["understaffed_intervals"] == 1
        assert result["summary"]["overstaffed_intervals"] == 1
        assert result["summary"]["balanced_intervals"] == 1
        assert result["summary"]["no_staff_intervals"] == 1
