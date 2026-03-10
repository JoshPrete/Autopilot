"""
Commercial target-gap helpers.

Turns recent revenue/labor/COGS performance into concrete gap-to-target math so
the product can say whether margin repair comes from labor, COGS, or revenue.
"""

from __future__ import annotations

from config.constants import (
    COGS_PCT_TARGET_HIGH,
    LABOR_PCT_TARGET_HIGH,
    NET_MARGIN_PCT_TARGET_LOW,
    PRIME_COST_PCT_TARGET_HIGH,
)


def _pct(cost_cents: int, revenue_cents: int) -> float | None:
    if revenue_cents <= 0:
        return None
    return round((cost_cents / revenue_cents) * 100, 2)


def _weekly_run_rate(amount_cents: int, days_count: int) -> int:
    if days_count <= 0:
        return 0
    return round((amount_cents / days_count) * 7)


def _weekly_cost_reduction_needed(
    weekly_cost_cents: int, weekly_revenue_cents: int, target_pct_high: float
) -> int:
    if weekly_revenue_cents <= 0:
        return 0
    allowed = weekly_revenue_cents * (target_pct_high / 100)
    return max(0, round(weekly_cost_cents - allowed))


def _weekly_revenue_needed_for_pct_target(
    weekly_cost_cents: int, weekly_revenue_cents: int, target_pct_high: float
) -> int:
    if weekly_cost_cents <= 0 or target_pct_high <= 0:
        return 0
    target_revenue = weekly_cost_cents / (target_pct_high / 100)
    return max(0, round(target_revenue - weekly_revenue_cents))


def _weekly_revenue_needed_for_margin_target(
    weekly_revenue_cents: int,
    weekly_cost_cents: int,
    target_margin_low: float,
) -> int:
    if target_margin_low >= 100:
        return 0
    target_revenue = weekly_cost_cents / (1 - (target_margin_low / 100))
    return max(0, round(target_revenue - weekly_revenue_cents))


def _trend_delta(current_pct: float | None, previous_pct: float | None) -> float | None:
    if current_pct is None or previous_pct is None:
        return None
    return round(current_pct - previous_pct, 2)


def _primary_lever(
    labor_gap_pp: float,
    cogs_gap_pp: float,
    prime_gap_pp: float,
    net_margin_pct: float | None,
) -> dict:
    if labor_gap_pp > 0.5 and cogs_gap_pp <= 0.5:
        return {
            "focus": "labor_efficiency",
            "reason": "Labor % is above target while COGS is within range.",
        }
    if cogs_gap_pp > 0.5 and labor_gap_pp <= 0.5:
        return {
            "focus": "cogs_control",
            "reason": "COGS % is above target while labor is within range.",
        }
    if labor_gap_pp > 0.5 and cogs_gap_pp > 0.5:
        return {
            "focus": "mixed_margin_repair",
            "reason": "Both labor % and COGS % are above target; staffing and pricing/mix need attention.",
        }
    if (
        prime_gap_pp <= 0
        and net_margin_pct is not None
        and net_margin_pct < NET_MARGIN_PCT_TARGET_LOW
    ):
        return {
            "focus": "revenue_growth",
            "reason": "Prime cost is within target but net margin is still low; grow revenue over the same cost base.",
        }
    return {
        "focus": "hold_or_refine",
        "reason": "Labor and COGS are near target bands; focus on marginal gains and monitoring.",
    }


def _truth_window(window: dict, financial_truth: dict | None) -> dict:
    window = window or {}
    financial_truth = financial_truth or {}

    days_count = int(window.get("days_count") or 0)
    revenue_cents = int(window.get("total_revenue_cents") or 0)
    labor_cents = int(window.get("total_labor_cost_cents") or 0)
    cogs_cents = int(window.get("total_cogs_cents") or 0)
    net_profit_cents = int(window.get("total_net_profit_cents") or 0)

    coverage_days = int(financial_truth.get("coverage_days") or 0)
    income_cents = (
        int(financial_truth.get("income_cents") or 0) if coverage_days > 0 else revenue_cents
    )
    expense_cents = (
        int(financial_truth.get("expense_cents") or 0)
        if coverage_days > 0
        else max(0, revenue_cents - net_profit_cents)
    )
    payroll_raw = financial_truth.get("payroll_cents")
    payroll_cents = (
        int(payroll_raw or 0) if coverage_days > 0 and payroll_raw not in (None, "") else None
    )
    labor_truth_cents = (
        payroll_cents if payroll_cents is not None and payroll_cents > 0 else labor_cents
    )
    labor_truth_source = (
        "xero_payroll"
        if payroll_cents is not None and payroll_cents > 0
        else "operational_labor_proxy"
    )
    overhead_proxy_cents = max(0, expense_cents - labor_truth_cents - cogs_cents)
    effective_days = coverage_days if coverage_days > 0 else days_count
    margin_basis_net_profit_cents = income_cents - expense_cents
    margin_basis_pct = (
        round((margin_basis_net_profit_cents / income_cents) * 100, 2) if income_cents > 0 else None
    )

    return {
        "coverage_days": coverage_days,
        "effective_days": effective_days,
        "income_cents": income_cents,
        "expense_cents": expense_cents,
        "payroll_cents": payroll_cents,
        "labor_truth_cents": labor_truth_cents,
        "labor_truth_source": labor_truth_source,
        "overhead_proxy_cents": overhead_proxy_cents,
        "overhead_proxy_pct": _pct(overhead_proxy_cents, income_cents),
        "margin_basis_net_profit_cents": margin_basis_net_profit_cents,
        "margin_basis_net_margin_pct": margin_basis_pct,
        "margin_basis_source": "xero_cash_truth" if coverage_days > 0 else "operational_proxy",
    }


def build_financial_target_gap(
    current_window: dict,
    previous_window: dict | None = None,
    current_financial_truth: dict | None = None,
    previous_financial_truth: dict | None = None,
) -> dict:
    current_window = current_window or {}
    previous_window = previous_window or {}

    days_count = int(current_window.get("days_count") or 0)
    revenue_cents = int(current_window.get("total_revenue_cents") or 0)
    labor_cents = int(current_window.get("total_labor_cost_cents") or 0)
    cogs_cents = int(current_window.get("total_cogs_cents") or 0)
    net_profit_cents = int(current_window.get("total_net_profit_cents") or 0)

    labor_pct = _pct(labor_cents, revenue_cents)
    cogs_pct = _pct(cogs_cents, revenue_cents)
    prime_cost_cents = labor_cents + cogs_cents
    prime_cost_pct = _pct(prime_cost_cents, revenue_cents)
    net_margin_pct = (
        round((net_profit_cents / revenue_cents) * 100, 2) if revenue_cents > 0 else None
    )

    prev_revenue_cents = int(previous_window.get("total_revenue_cents") or 0)
    prev_labor_cents = int(previous_window.get("total_labor_cost_cents") or 0)
    prev_cogs_cents = int(previous_window.get("total_cogs_cents") or 0)
    prev_prime_cost_cents = prev_labor_cents + prev_cogs_cents

    prev_labor_pct = _pct(prev_labor_cents, prev_revenue_cents)
    prev_cogs_pct = _pct(prev_cogs_cents, prev_revenue_cents)
    prev_prime_cost_pct = _pct(prev_prime_cost_cents, prev_revenue_cents)

    current_truth = _truth_window(current_window, current_financial_truth)
    previous_truth = _truth_window(previous_window, previous_financial_truth)

    weekly_revenue_cents = _weekly_run_rate(revenue_cents, days_count)
    weekly_labor_cents = _weekly_run_rate(labor_cents, days_count)
    weekly_cogs_cents = _weekly_run_rate(cogs_cents, days_count)
    weekly_prime_cost_cents = weekly_labor_cents + weekly_cogs_cents
    weekly_net_profit_cents = _weekly_run_rate(net_profit_cents, days_count)
    weekly_margin_revenue_cents = _weekly_run_rate(
        current_truth["income_cents"], current_truth["effective_days"]
    )
    weekly_margin_total_cost_cents = _weekly_run_rate(
        current_truth["expense_cents"], current_truth["effective_days"]
    )
    weekly_overhead_cents = _weekly_run_rate(
        current_truth["overhead_proxy_cents"], current_truth["effective_days"]
    )

    labor_gap_pp = (
        round(max(0.0, (labor_pct or 0.0) - LABOR_PCT_TARGET_HIGH), 2)
        if labor_pct is not None
        else 0.0
    )
    cogs_gap_pp = (
        round(max(0.0, (cogs_pct or 0.0) - COGS_PCT_TARGET_HIGH), 2)
        if cogs_pct is not None
        else 0.0
    )
    prime_gap_pp = (
        round(max(0.0, (prime_cost_pct or 0.0) - PRIME_COST_PCT_TARGET_HIGH), 2)
        if prime_cost_pct is not None
        else 0.0
    )
    net_margin_gap_pp = (
        round(
            max(
                0.0,
                NET_MARGIN_PCT_TARGET_LOW - (current_truth["margin_basis_net_margin_pct"] or 0.0),
            ),
            2,
        )
        if current_truth["margin_basis_net_margin_pct"] is not None
        else 0.0
    )

    primary = _primary_lever(
        labor_gap_pp,
        cogs_gap_pp,
        prime_gap_pp,
        current_truth["margin_basis_net_margin_pct"],
    )

    return {
        "targets": {
            "labor_pct_high": LABOR_PCT_TARGET_HIGH,
            "cogs_pct_high": COGS_PCT_TARGET_HIGH,
            "prime_cost_pct_high": PRIME_COST_PCT_TARGET_HIGH,
            "net_margin_pct_low": NET_MARGIN_PCT_TARGET_LOW,
        },
        "current": {
            "days_count": days_count,
            "revenue_cents": revenue_cents,
            "labor_cents": labor_cents,
            "cogs_cents": cogs_cents,
            "prime_cost_cents": prime_cost_cents,
            "net_profit_cents": net_profit_cents,
            "labor_pct": labor_pct,
            "cogs_pct": cogs_pct,
            "prime_cost_pct": prime_cost_pct,
            "net_margin_pct": net_margin_pct,
            "margin_basis_net_margin_pct": current_truth["margin_basis_net_margin_pct"],
            "margin_basis_net_profit_cents": current_truth["margin_basis_net_profit_cents"],
            "margin_basis_source": current_truth["margin_basis_source"],
            "margin_basis_income_cents": current_truth["income_cents"],
            "margin_basis_expense_cents": current_truth["expense_cents"],
            "labor_truth_cents": current_truth["labor_truth_cents"],
            "labor_truth_source": current_truth["labor_truth_source"],
            "operating_overhead_cents": current_truth["overhead_proxy_cents"],
            "operating_overhead_pct": current_truth["overhead_proxy_pct"],
            "truth_coverage_days": current_truth["coverage_days"],
        },
        "run_rate": {
            "weekly_revenue_cents": weekly_revenue_cents,
            "weekly_labor_cents": weekly_labor_cents,
            "weekly_cogs_cents": weekly_cogs_cents,
            "weekly_prime_cost_cents": weekly_prime_cost_cents,
            "weekly_net_profit_cents": weekly_net_profit_cents,
            "weekly_margin_revenue_cents": weekly_margin_revenue_cents,
            "weekly_margin_total_cost_cents": weekly_margin_total_cost_cents,
            "weekly_overhead_cents": weekly_overhead_cents,
        },
        "trend": {
            "labor_pct_delta_pp": _trend_delta(labor_pct, prev_labor_pct),
            "cogs_pct_delta_pp": _trend_delta(cogs_pct, prev_cogs_pct),
            "prime_cost_pct_delta_pp": _trend_delta(prime_cost_pct, prev_prime_cost_pct),
            "margin_basis_net_margin_delta_pp": _trend_delta(
                current_truth["margin_basis_net_margin_pct"],
                previous_truth["margin_basis_net_margin_pct"],
            ),
        },
        "gaps": {
            "labor_pct_gap_pp": labor_gap_pp,
            "cogs_pct_gap_pp": cogs_gap_pp,
            "prime_cost_pct_gap_pp": prime_gap_pp,
            "net_margin_gap_pp": net_margin_gap_pp,
            "weekly_overhead_absorption_cents": weekly_overhead_cents,
            "weekly_labor_reduction_needed_cents": _weekly_cost_reduction_needed(
                weekly_labor_cents,
                weekly_revenue_cents,
                LABOR_PCT_TARGET_HIGH,
            ),
            "weekly_cogs_reduction_needed_cents": _weekly_cost_reduction_needed(
                weekly_cogs_cents,
                weekly_revenue_cents,
                COGS_PCT_TARGET_HIGH,
            ),
            "weekly_prime_cost_reduction_needed_cents": _weekly_cost_reduction_needed(
                weekly_prime_cost_cents,
                weekly_revenue_cents,
                PRIME_COST_PCT_TARGET_HIGH,
            ),
            "weekly_revenue_needed_for_labor_target_cents": _weekly_revenue_needed_for_pct_target(
                weekly_labor_cents,
                weekly_revenue_cents,
                LABOR_PCT_TARGET_HIGH,
            ),
            "weekly_revenue_needed_for_cogs_target_cents": _weekly_revenue_needed_for_pct_target(
                weekly_cogs_cents,
                weekly_revenue_cents,
                COGS_PCT_TARGET_HIGH,
            ),
            "weekly_revenue_needed_for_prime_target_cents": _weekly_revenue_needed_for_pct_target(
                weekly_prime_cost_cents,
                weekly_revenue_cents,
                PRIME_COST_PCT_TARGET_HIGH,
            ),
            "weekly_revenue_needed_for_net_margin_target_cents": _weekly_revenue_needed_for_margin_target(
                weekly_margin_revenue_cents,
                max(weekly_margin_total_cost_cents, weekly_prime_cost_cents),
                NET_MARGIN_PCT_TARGET_LOW,
            ),
        },
        "primary_lever": primary,
    }
