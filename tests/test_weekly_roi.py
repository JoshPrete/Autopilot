from datetime import date

from analysis.reporting import generate_weekly_roi_report


class TestWeeklyROIReport:
    def test_generates_week_over_week_deltas(self, monkeypatch):
        current_rows = [
            {
                "date": "2026-02-10",
                "revenue_cents": 100_000,
                "labor_cost_cents": 30_000,
                "cogs_cents": 25_000,
                "net_profit_cents": 45_000,
                "labor_pct": 30.0,
                "revenue_per_labor_hour": 5_000,
            },
            {
                "date": "2026-02-11",
                "revenue_cents": 120_000,
                "labor_cost_cents": 36_000,
                "cogs_cents": 30_000,
                "net_profit_cents": 54_000,
                "labor_pct": 30.0,
                "revenue_per_labor_hour": 5_200,
            },
        ]
        previous_rows = [
            {
                "date": "2026-02-03",
                "revenue_cents": 90_000,
                "labor_cost_cents": 29_700,
                "cogs_cents": 24_300,
                "net_profit_cents": 36_000,
                "labor_pct": 33.0,
                "revenue_per_labor_hour": 4_500,
            },
            {
                "date": "2026-02-04",
                "revenue_cents": 95_000,
                "labor_cost_cents": 30_400,
                "cogs_cents": 25_600,
                "net_profit_cents": 39_000,
                "labor_pct": 32.0,
                "revenue_per_labor_hour": 4_700,
            },
        ]

        def _fake_get_daily_profitability(_site_id, start_date, _end_date):
            if start_date == date(2026, 2, 9):
                return current_rows
            return previous_rows

        monkeypatch.setattr("analysis.reporting.get_daily_profitability", _fake_get_daily_profitability)

        result = generate_weekly_roi_report(
            site_id="site-1",
            site_name="Clubhouse",
            week_end=date(2026, 2, 15),
        )

        assert result["current_week"]["total_revenue_cents"] == 220_000
        assert result["previous_week"]["total_revenue_cents"] == 185_000
        assert result["deltas"]["revenue_cents_delta"] == 35_000
        assert result["deltas"]["net_profit_cents_delta"] == 24_000
        assert result["deltas"]["labor_pct_delta_pp"] == -2.5
        assert result["deltas"]["revenue_per_labor_hour_delta_pct"] == 10.87
        assert "Net profit improved" in result["headline"]
        assert "WEEKLY ROI" in result["report_text"]

    def test_handles_missing_previous_week_data(self, monkeypatch):
        current_rows = [
            {
                "date": "2026-02-10",
                "revenue_cents": 80_000,
                "labor_cost_cents": 24_000,
                "cogs_cents": 20_000,
                "net_profit_cents": 36_000,
                "labor_pct": 30.0,
                "revenue_per_labor_hour": 4_800,
            }
        ]

        def _fake_get_daily_profitability(_site_id, start_date, _end_date):
            if start_date == date(2026, 2, 9):
                return current_rows
            return []

        monkeypatch.setattr("analysis.reporting.get_daily_profitability", _fake_get_daily_profitability)

        result = generate_weekly_roi_report(
            site_id="site-1",
            site_name="Clubhouse",
            week_end=date(2026, 2, 15),
        )

        assert result["previous_week"]["days"] == 0
        assert result["deltas"]["net_profit_cents_delta"] is None
        assert result["headline"] == "Current week profitability baseline generated."
