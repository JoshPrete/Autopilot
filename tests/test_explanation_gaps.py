from analysis.explanation_gaps import (
    detect_purchase_explanation_gaps,
    detect_wage_explanation_gaps,
)


def test_detect_purchase_explanation_gaps_flags_recurring_unmapped_supplier_line():
    gaps = detect_purchase_explanation_gaps(
        "site-1",
        review_items=[
            {
                "review_id": 11,
                "reason_code": "UNMAPPED",
                "supplier": "DairyCo",
                "line_description": "Oat Milk 12x1L",
                "line_total": 86.40,
                "bill_date": "2026-02-18",
            },
            {
                "review_id": 12,
                "reason_code": "LOW_CONFIDENCE",
                "supplier": "DairyCo",
                "line_description": "Oat Milk 12x1L",
                "line_total": 86.40,
                "bill_date": "2026-02-25",
            },
        ],
        operator_rules=[],
    )

    assert gaps
    first = gaps[0]
    assert first["agenda_type"] == "purchase_explanation"
    assert first["priority"] == "high"
    assert "what is `Oat Milk 12x1L` from DairyCo for".lower() in first["question"].lower()
    assert first["evidence"]["occurrences"] == 2


def test_detect_wage_explanation_gaps_flags_recent_spike():
    gaps = detect_wage_explanation_gaps(
        "site-1",
        bottom_line_scorecard={
            "targets": {
                "targets": {"labor_pct_high": 28.0},
                "gaps": {"weekly_labor_reduction_needed_cents": 18200},
            },
            "financial_truth": {"labor_truth_source": "xero_payroll"},
        },
        daily_profitability=[
            {
                "date": "2026-02-16",
                "revenue_cents": 165000,
                "labor_cost_cents": 42000,
                "labor_pct": 25.45,
            },
            {
                "date": "2026-02-17",
                "revenue_cents": 122000,
                "labor_cost_cents": 47000,
                "labor_pct": 38.52,
            },
            {
                "date": "2026-02-18",
                "revenue_cents": 170000,
                "labor_cost_cents": 45500,
                "labor_pct": 26.76,
            },
        ],
    )

    assert gaps
    first = gaps[0]
    assert first["agenda_type"] == "labor_explanation"
    assert "Wage % hit 38.5% on 2026-02-17" in first["question"]
    assert first["priority"] == "high"
    assert first["evidence"]["labor_truth_source"] == "xero_payroll"
