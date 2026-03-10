from analysis.financial_targets import build_financial_target_gap


def test_build_financial_target_gap_identifies_labor_lever():
    result = build_financial_target_gap(
        current_window={
            "days_count": 7,
            "total_revenue_cents": 1_400_000,
            "total_labor_cost_cents": 490_000,
            "total_cogs_cents": 350_000,
            "total_net_profit_cents": 280_000,
        },
        previous_window={
            "days_count": 7,
            "total_revenue_cents": 1_330_000,
            "total_labor_cost_cents": 420_000,
            "total_cogs_cents": 340_000,
            "total_net_profit_cents": 250_000,
        },
    )

    assert result["primary_lever"]["focus"] == "labor_efficiency"
    assert result["current"]["labor_pct"] == 35.0
    assert result["current"]["cogs_pct"] == 25.0
    assert result["gaps"]["weekly_labor_reduction_needed_cents"] == 98_000
    assert result["gaps"]["weekly_revenue_needed_for_labor_target_cents"] == 350_000


def test_build_financial_target_gap_identifies_revenue_growth_when_prime_cost_ok():
    result = build_financial_target_gap(
        current_window={
            "days_count": 7,
            "total_revenue_cents": 1_000_000,
            "total_labor_cost_cents": 260_000,
            "total_cogs_cents": 300_000,
            "total_net_profit_cents": 80_000,
        },
        previous_window={
            "days_count": 7,
            "total_revenue_cents": 980_000,
            "total_labor_cost_cents": 255_000,
            "total_cogs_cents": 295_000,
            "total_net_profit_cents": 90_000,
        },
    )

    assert result["current"]["prime_cost_pct"] == 56.0
    assert result["primary_lever"]["focus"] == "revenue_growth"
    assert result["gaps"]["weekly_prime_cost_reduction_needed_cents"] == 0
    assert result["gaps"]["weekly_revenue_needed_for_prime_target_cents"] == 0
    assert result["gaps"]["weekly_revenue_needed_for_net_margin_target_cents"] > 0


def test_build_financial_target_gap_uses_xero_truth_for_overhead_absorption():
    result = build_financial_target_gap(
        current_window={
            "days_count": 7,
            "total_revenue_cents": 1_000_000,
            "total_labor_cost_cents": 260_000,
            "total_cogs_cents": 300_000,
            "total_net_profit_cents": 80_000,
        },
        previous_window={
            "days_count": 7,
            "total_revenue_cents": 980_000,
            "total_labor_cost_cents": 255_000,
            "total_cogs_cents": 295_000,
            "total_net_profit_cents": 90_000,
        },
        current_financial_truth={
            "coverage_days": 7,
            "income_cents": 1_000_000,
            "expense_cents": 950_000,
            "payroll_cents": 260_000,
        },
        previous_financial_truth={
            "coverage_days": 7,
            "income_cents": 980_000,
            "expense_cents": 900_000,
            "payroll_cents": 255_000,
        },
    )

    assert result["current"]["margin_basis_source"] == "xero_cash_truth"
    assert result["current"]["margin_basis_net_margin_pct"] == 5.0
    assert result["current"]["operating_overhead_cents"] == 390_000
    assert result["gaps"]["weekly_overhead_absorption_cents"] == 390_000
    assert result["gaps"]["weekly_revenue_needed_for_net_margin_target_cents"] == 55_556
    assert result["primary_lever"]["focus"] == "revenue_growth"
