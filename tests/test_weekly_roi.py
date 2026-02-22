from datetime import date

from analysis.reporting import format_weekly_roi_sms, generate_weekly_roi_report


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
                "labor_hours": 20.0,
            },
            {
                "date": "2026-02-11",
                "revenue_cents": 120_000,
                "labor_cost_cents": 36_000,
                "cogs_cents": 30_000,
                "net_profit_cents": 54_000,
                "labor_pct": 30.0,
                "revenue_per_labor_hour": 5_200,
                "labor_hours": 23.0,
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
                "labor_hours": 20.0,
            },
            {
                "date": "2026-02-04",
                "revenue_cents": 95_000,
                "labor_cost_cents": 30_400,
                "cogs_cents": 25_600,
                "net_profit_cents": 39_000,
                "labor_pct": 32.0,
                "revenue_per_labor_hour": 4_700,
                "labor_hours": 20.0,
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
        assert abs(result["deltas"]["labor_pct_delta_pp"] - (-2.5)) < 0.05
        assert result["deltas"]["revenue_per_labor_hour_delta_pct"] == 10.62
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


class TestWeeklyROISMS:
    def test_formats_sms_with_deltas(self):
        report = {
            "week_start": "2026-02-09",
            "week_end": "2026-02-15",
            "headline": "Net profit improved by $240 week-over-week.",
            "deltas": {
                "net_profit_cents_delta": 24_000,
                "labor_pct_delta_pp": -2.5,
                "revenue_per_labor_hour_delta_pct": 10.87,
            },
        }

        sms = format_weekly_roi_sms(report)

        assert "Weekly ROI" in sms
        assert "2026-02-09 to 2026-02-15" in sms
        assert "Net profit WoW: +$240" in sms
        assert "Labor % delta: -2.5pp" in sms
        assert "Rev/labor hr delta: +10.9%" in sms
