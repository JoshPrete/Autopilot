"""
Tests for analysis/business_intelligence.py

Uses synthetic data — no DB required.
"""

import pytest
from analysis.business_intelligence import (
    _linear_trend,
    _cv,
    _confidence,
    _h_revenue_concentration,
    _h_quiet_window,
    _h_dow_efficiency,
    _h_labour_trend,
    _h_avg_ticket_trend,
    _h_modifier_penetration,
    _h_product_mix_shift,
    format_hypotheses_for_chat,
)


# ── Maths helpers ──────────────────────────────────────────────────────────

def test_linear_trend_rising():
    slope, r_sq = _linear_trend([1, 2, 3, 4, 5])
    assert slope > 0
    assert r_sq > 0.95


def test_linear_trend_falling():
    slope, r_sq = _linear_trend([5, 4, 3, 2, 1])
    assert slope < 0
    assert r_sq > 0.95


def test_linear_trend_flat():
    slope, r_sq = _linear_trend([3, 3, 3, 3, 3])
    assert slope == 0.0


def test_linear_trend_too_short():
    slope, r_sq = _linear_trend([1, 2])
    assert slope == 0.0
    assert r_sq == 0.0


def test_cv_consistent():
    assert _cv([10, 10, 10, 10]) == 0.0


def test_cv_variable():
    assert _cv([1, 10, 1, 10]) > 0.5


def test_confidence_many_days_consistent():
    score, label = _confidence(n_days=28, cv=0.1)
    assert label == "strong"
    assert score >= 0.65


def test_confidence_few_days():
    score, label = _confidence(n_days=5, cv=0.5)
    assert label in ("moderate", "weak")


# ── Revenue concentration ──────────────────────────────────────────────────

_HOURLY_CONCENTRATED = {
    7:  {"revenue_cents": 5_000,  "order_count": 10, "day_count": 20},
    8:  {"revenue_cents": 30_000, "order_count": 60, "day_count": 20},
    9:  {"revenue_cents": 28_000, "order_count": 55, "day_count": 20},
    10: {"revenue_cents": 10_000, "order_count": 20, "day_count": 20},
    11: {"revenue_cents": 8_000,  "order_count": 16, "day_count": 20},
    14: {"revenue_cents": 4_000,  "order_count": 8,  "day_count": 20},
}

def test_revenue_concentration_detected():
    h = _h_revenue_concentration(_HOURLY_CONCENTRATED)
    assert h is not None
    assert h["hypothesis_key"] == "revenue_concentration_peak"
    assert "8am" in h["statement"]
    assert h["evidence"]["revenue_share_pct"] > 30


def test_revenue_concentration_no_data():
    assert _h_revenue_concentration({}) is None


def test_revenue_concentration_flat_not_flagged():
    even = {h: {"revenue_cents": 10_000, "order_count": 20, "day_count": 20}
            for h in range(6, 18)}
    result = _h_revenue_concentration(even)
    # Share per 2-hour window is ~17% — below 25% threshold
    assert result is None


# ── Quiet window ───────────────────────────────────────────────────────────

def test_quiet_window_detected():
    h = _h_quiet_window(_HOURLY_CONCENTRATED)
    assert h is not None
    assert h["hypothesis_key"] == "quiet_prep_window"
    assert "14" not in h["statement"] or "2pm" in h["statement"]


def test_quiet_window_no_afternoon_data():
    morning_only = {h: {"revenue_cents": 5_000, "order_count": 10, "day_count": 10}
                    for h in range(7, 12)}
    assert _h_quiet_window(morning_only) is None


# ── DOW efficiency ─────────────────────────────────────────────────────────

_CORR_GOOD = {
    "by_dow": [
        {"day_name": "Monday", "rev_per_labor_dollar": 3.2},
        {"day_name": "Tuesday", "rev_per_labor_dollar": 3.5},
        {"day_name": "Wednesday", "rev_per_labor_dollar": 3.8},
        {"day_name": "Thursday", "rev_per_labor_dollar": 4.1},
        {"day_name": "Friday", "rev_per_labor_dollar": 4.5},
        {"day_name": "Saturday", "rev_per_labor_dollar": 2.4},  # worst
    ]
}

def test_dow_efficiency_detected():
    h = _h_dow_efficiency(_CORR_GOOD)
    assert h is not None
    assert "Saturday" in h["statement"]
    assert "Friday" in h["statement"]


def test_dow_efficiency_no_gap():
    flat = {"by_dow": [
        {"day_name": "Monday", "rev_per_labor_dollar": 3.0},
        {"day_name": "Tuesday", "rev_per_labor_dollar": 3.1},
        {"day_name": "Wednesday", "rev_per_labor_dollar": 3.0},
    ]}
    assert _h_dow_efficiency(flat) is None


def test_dow_efficiency_too_few_days():
    assert _h_dow_efficiency({"by_dow": [
        {"day_name": "Monday", "rev_per_labor_dollar": 3.0},
        {"day_name": "Tuesday", "rev_per_labor_dollar": 5.0},
    ]}) is None


# ── Labour trend ───────────────────────────────────────────────────────────

def _daily_rows(labor_pcts: list[float]) -> list[dict]:
    return [
        {"labor_pct": p, "labor_data_quality": "trusted", "revenue_cents": 100_000}
        for p in labor_pcts
    ]

def test_labour_trend_rising():
    h = _h_labour_trend(_daily_rows([27, 28, 29, 30, 31, 32, 33, 34, 35]))
    assert h is not None
    assert "risen" in h["statement"]
    assert h["evidence"]["direction"] == "risen"


def test_labour_trend_falling():
    h = _h_labour_trend(_daily_rows([38, 37, 36, 35, 34, 33, 32, 31, 30]))
    assert h is not None
    assert "fallen" in h["statement"]


def test_labour_trend_flat_not_flagged():
    assert _h_labour_trend(_daily_rows([30, 30, 30, 30, 30, 30, 30])) is None


def test_labour_trend_too_few_rows():
    assert _h_labour_trend(_daily_rows([28, 32])) is None


# ── Avg ticket trend ───────────────────────────────────────────────────────

def _ticket_rows(avg_tickets_cents: list[float]) -> list[dict]:
    orders = 80
    return [{"revenue_cents": int(t * orders), "order_count": orders}
            for t in avg_tickets_cents]

def test_ticket_trend_rising():
    h = _h_avg_ticket_trend(_ticket_rows([400, 410, 420, 430, 440, 450, 460, 470]))
    assert h is not None
    assert "increased" in h["statement"]


def test_ticket_trend_falling():
    h = _h_avg_ticket_trend(_ticket_rows([500, 490, 480, 470, 460, 450, 440, 430]))
    assert h is not None
    assert "decreased" in h["statement"]


def test_ticket_trend_flat():
    assert _h_avg_ticket_trend(_ticket_rows([450] * 10)) is None


# ── Modifier penetration ───────────────────────────────────────────────────

def test_modifier_high_alt_milk():
    h = _h_modifier_penetration(total=200, alt_milk=90, extra_shot=20)
    assert h is not None
    assert "45.0%" in h["statement"]
    assert h["hypothesis_key"] == "modifier_alt_milk_penetration"


def test_modifier_low_alt_milk():
    assert _h_modifier_penetration(total=200, alt_milk=10, extra_shot=5) is None


def test_modifier_too_few_drinks():
    assert _h_modifier_penetration(total=10, alt_milk=8, extra_shot=2) is None


# ── Product mix shift ──────────────────────────────────────────────────────

_MIX = {
    "latte": {"recent_qty": 120, "prior_qty": 80, "item_name": "Latte"},    # +growing
    "flat_white": {"recent_qty": 60, "prior_qty": 80, "item_name": "Flat White"},  # falling
    "espresso": {"recent_qty": 20, "prior_qty": 18, "item_name": "Espresso"},      # stable
}

def test_mix_shift_detected():
    results = _h_product_mix_shift(_MIX)
    assert len(results) >= 1
    keys = [r["hypothesis_key"] for r in results]
    assert any("latte" in k or "flat_white" in k for k in keys)


def test_mix_shift_empty():
    assert _h_product_mix_shift({}) == []


def test_mix_shift_stable_not_flagged():
    stable = {
        "latte": {"recent_qty": 100, "prior_qty": 100, "item_name": "Latte"},
        "flat_white": {"recent_qty": 80, "prior_qty": 82, "item_name": "Flat White"},
    }
    assert _h_product_mix_shift(stable) == []


# ── format_hypotheses_for_chat ─────────────────────────────────────────────

def test_format_chat_excludes_weak():
    hypotheses = [
        {"confidence_label": "strong", "statement": "Strong insight."},
        {"confidence_label": "moderate", "statement": "Moderate insight."},
        {"confidence_label": "weak", "statement": "Weak insight."},
    ]
    result = format_hypotheses_for_chat(hypotheses)
    assert "Strong insight" in result
    assert "Moderate insight" in result
    assert "Weak insight" not in result


def test_format_chat_respects_max():
    hypotheses = [
        {"confidence_label": "strong", "statement": f"Insight {i}."}
        for i in range(10)
    ]
    result = format_hypotheses_for_chat(hypotheses, max_items=3)
    assert result.count("Insight") == 3


def test_format_chat_empty():
    assert format_hypotheses_for_chat([]) == ""
