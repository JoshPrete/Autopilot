from datetime import date

from app.freshness_recovery import plan_freshness_recovery, recover_site_freshness


def test_plan_freshness_recovery_builds_backfill_window():
    health = {
        "components": [
            {"source": "square_orders", "latest_date": "2026-03-07", "status": "red"},
            {
                "source": "deputy_rosters",
                "latest_date": "2026-03-08",
                "status": "yellow",
                "next_14d_shifts": 0,
            },
            {"source": "daily_profitability", "latest_date": "2026-03-07", "status": "red"},
        ]
    }

    plan = plan_freshness_recovery(health, today=date(2026, 3, 18))

    assert plan["should_run"] is True
    assert plan["run_square"] is True
    assert plan["run_deputy"] is True
    assert plan["run_profitability"] is True
    assert plan["square_start"] == "2026-03-08"
    assert plan["deputy_start"] == "2026-03-09"
    assert plan["profitability_start"] == "2026-03-08"
    assert plan["recovery_start"] == "2026-03-08"
    assert plan["recovery_end"] == "2026-03-18"
    assert plan["recovery_days"] == 11


def test_plan_freshness_recovery_clips_long_backfill_window():
    health = {
        "components": [
            {"source": "square_orders", "latest_date": "2026-02-20", "status": "red"},
            {
                "source": "deputy_rosters",
                "latest_date": "2026-03-18",
                "status": "green",
                "next_14d_shifts": 12,
            },
            {"source": "daily_profitability", "latest_date": "2026-02-21", "status": "red"},
        ]
    }

    plan = plan_freshness_recovery(
        health,
        today=date(2026, 3, 18),
        max_backfill_days=7,
    )

    assert plan["square_start"] == "2026-03-12"
    assert plan["profitability_start"] == "2026-03-12"
    assert plan["recovery_start"] == "2026-03-12"
    assert plan["recovery_days"] == 7
    assert plan["clipped_to_max_days"] is True


def test_recover_site_freshness_runs_deputy_once_and_replays_days():
    calls: list[tuple[str, date]] = []

    def fake_step_ingest(site_id: str, run_date: date, dry_run: bool) -> dict:
        calls.append(("ingest", run_date))
        assert site_id == "site-1"
        assert dry_run is False
        return {"status": "ok", "orders": 10}

    def fake_step_deputy(site_id: str, run_date: date, dry_run: bool) -> dict:
        calls.append(("deputy", run_date))
        assert site_id == "site-1"
        assert dry_run is False
        return {"status": "ok", "rosters": 5}

    def fake_step_profitability(site_id: str, run_date: date, dry_run: bool) -> dict:
        calls.append(("profitability", run_date))
        assert site_id == "site-1"
        assert dry_run is False
        return {"status": "ok", "revenue_cents": 1000}

    result = recover_site_freshness(
        "site-1",
        today=date(2026, 3, 18),
        data_health={
            "components": [
                {"source": "square_orders", "latest_date": "2026-03-16", "status": "red"},
                {
                    "source": "deputy_rosters",
                    "latest_date": "2026-03-15",
                    "status": "yellow",
                    "next_14d_shifts": 0,
                },
                {
                    "source": "daily_profitability",
                    "latest_date": "2026-03-16",
                    "status": "red",
                },
            ]
        },
        step_ingest_fn=fake_step_ingest,
        step_deputy_fn=fake_step_deputy,
        step_profitability_fn=fake_step_profitability,
    )

    assert result["status"] == "ok"
    assert result["plan"]["recovery_start"] == "2026-03-16"
    assert calls[0] == ("deputy", date(2026, 3, 16))
    assert calls[1:] == [
        ("ingest", date(2026, 3, 17)),
        ("ingest", date(2026, 3, 18)),
        ("profitability", date(2026, 3, 16)),
        ("profitability", date(2026, 3, 17)),
        ("profitability", date(2026, 3, 18)),
    ]


def test_recover_site_freshness_skips_profitability_when_ingest_errors():
    calls: list[tuple[str, date]] = []

    def fake_step_ingest(site_id: str, run_date: date, dry_run: bool) -> dict:
        calls.append(("ingest", run_date))
        return {"status": "error", "error": "square api down"}

    def fake_step_deputy(site_id: str, run_date: date, dry_run: bool) -> dict:
        return {"status": "ok"}

    def fake_step_profitability(site_id: str, run_date: date, dry_run: bool) -> dict:
        calls.append(("profitability", run_date))
        return {"status": "ok"}

    result = recover_site_freshness(
        "site-1",
        today=date(2026, 3, 18),
        data_health={
            "components": [
                {"source": "square_orders", "latest_date": "2026-03-17", "status": "red"},
                {
                    "source": "deputy_rosters",
                    "latest_date": "2026-03-18",
                    "status": "green",
                    "next_14d_shifts": 4,
                },
                {
                    "source": "daily_profitability",
                    "latest_date": "2026-03-17",
                    "status": "red",
                },
            ]
        },
        step_ingest_fn=fake_step_ingest,
        step_deputy_fn=fake_step_deputy,
        step_profitability_fn=fake_step_profitability,
    )

    assert result["status"] == "partial"
    assert calls == [("ingest", date(2026, 3, 18))]
    assert result["profitability"] == []
