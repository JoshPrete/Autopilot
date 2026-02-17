from analysis.reporting import generate_pilot_kpi_snapshot


class TestPilotKpiSnapshot:
    def test_builds_snapshot_from_roi_and_weekly_stats(self, monkeypatch):
        monkeypatch.setattr(
            "analysis.reporting.generate_weekly_roi_report",
            lambda **_kwargs: {
                "week_start": "2026-02-09",
                "week_end": "2026-02-15",
                "headline": "Net profit improved by $240 week-over-week.",
                "current_week": {
                    "avg_labor_pct": 30.5,
                    "avg_revenue_per_labor_hour_cents": 5100,
                    "total_net_profit_cents": 540000,
                    "total_revenue_cents": 1800000,
                },
                "deltas": {
                    "net_profit_cents_delta": 24000,
                    "labor_pct_delta_pp": -2.5,
                    "revenue_per_labor_hour_delta_pct": 10.87,
                },
            },
        )
        monkeypatch.setattr(
            "analysis.reporting.get_weekly_stats",
            lambda *_args, **_kwargs: {
                "avg_prediction_accuracy": 86.2,
                "adoption_rate": 0.71,
                "recommendations_total": 14,
                "recommendations_adopted": 10,
            },
        )
        monkeypatch.setattr(
            "analysis.reporting._get_pipeline_coverage",
            lambda *_args, **_kwargs: {
                "expected_days": 7,
                "orders_days": 7,
                "deputy_days": 6,
                "profitability_days": 7,
                "prediction_days": 7,
                "pipeline_success_pct_estimate": 96.4,
            },
        )

        snapshot = generate_pilot_kpi_snapshot(
            site_id="site-1",
            site_name="Clubhouse",
        )

        assert snapshot["site_id"] == "site-1"
        assert snapshot["business_kpis"]["weekly_net_profit_cents"] == 540000
        assert snapshot["business_kpis"]["labor_pct_wow_delta_pp"] == -2.5
        assert snapshot["ops_kpis"]["avg_prediction_accuracy_pct"] == 86.2
        assert snapshot["reliability"]["pipeline_success_pct_estimate"] == 96.4
        assert snapshot["headline"].startswith("Net profit improved")
