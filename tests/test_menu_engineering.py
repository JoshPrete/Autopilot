"""
Tests for analysis/menu_engineering.py

Uses monkey-patching to inject fixture margin data without a real DB.
"""

import pytest
from unittest.mock import patch

from analysis.menu_engineering import compute_menu_matrix, _median


# ---------------------------------------------------------------------------
# Unit helpers
# ---------------------------------------------------------------------------

def test_median_odd():
    assert _median([1, 3, 5]) == 3


def test_median_even():
    assert _median([1, 2, 3, 4]) == 2.5


def test_median_single():
    assert _median([7]) == 7


def test_median_empty():
    assert _median([]) == 0.0


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FIXTURE_MARGINS = [
    # high pop, high margin → star
    {"item": "Flat White", "score_key": "flat_white", "category": "drink",
     "qty": 200, "avg_price_cents": 550, "cogs_cents": 80, "margin_pct": 85.5,
     "total_profit_cents": 94000},
    # high pop, low margin → cash_cow
    {"item": "Batch Brew", "score_key": "batch_brew", "category": "drink",
     "qty": 150, "avg_price_cents": 380, "cogs_cents": 120, "margin_pct": 55.0,
     "total_profit_cents": 39000},
    # low pop, high margin → question_mark
    {"item": "Cold Brew", "score_key": "cold_brew", "category": "drink",
     "qty": 30, "avg_price_cents": 700, "cogs_cents": 90, "margin_pct": 87.1,
     "total_profit_cents": 18300},
    # low pop, low margin → laggard
    {"item": "Hot Choc", "score_key": "hot_choc", "category": "drink",
     "qty": 20, "avg_price_cents": 490, "cogs_cents": 200, "margin_pct": 42.0,
     "total_profit_cents": 5800},
]


def _patch_margins(margins):
    return patch("analysis.menu_engineering.compute_item_margins", return_value=margins)


# ---------------------------------------------------------------------------
# Quadrant classification
# ---------------------------------------------------------------------------

def test_quadrant_classification():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1", days=28)

    items = {i["score_key"]: i for i in result["items"]}
    assert items["flat_white"]["quadrant"] == "star"
    assert items["batch_brew"]["quadrant"] == "cash_cow"
    assert items["cold_brew"]["quadrant"] == "question_mark"
    assert items["hot_choc"]["quadrant"] == "laggard"


def test_quadrant_labels_present():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1")
    for item in result["items"]:
        assert item["quadrant_label"] in ("Star", "Cash Cow", "Question Mark", "Laggard")


def test_recommendation_present():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1")
    for item in result["items"]:
        assert len(item["recommendation"]) > 10


# ---------------------------------------------------------------------------
# Sale profile enrichment
# ---------------------------------------------------------------------------

def test_sale_profile_family_inferred():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1")
    items = {i["score_key"]: i for i in result["items"]}
    assert items["flat_white"]["sale_profile"]["family"] == "flat_white"
    assert items["cold_brew"]["sale_profile"]["family"] == "cold_brew"


# ---------------------------------------------------------------------------
# Quadrant summary
# ---------------------------------------------------------------------------

def test_quadrant_summary_counts():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1")
    qs = result["quadrant_summary"]
    assert qs["star"]["count"] == 1
    assert qs["cash_cow"]["count"] == 1
    assert qs["question_mark"]["count"] == 1
    assert qs["laggard"]["count"] == 1


def test_quadrant_summary_profit():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1")
    qs = result["quadrant_summary"]
    assert qs["star"]["total_profit_cents"] == 94000
    assert qs["laggard"]["total_profit_cents"] == 5800


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

def test_thresholds_correct():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1")
    t = result["thresholds"]
    # qty median of [200, 150, 30, 20] = (150+30)/2 = 90
    assert t["popularity_median"] == 90.0
    # margin_pct median of [85.5, 55.0, 87.1, 42.0] = (85.5+55.0)/2 = 70.25
    assert t["margin_median"] == 70.2


# ---------------------------------------------------------------------------
# Sort order (stars first)
# ---------------------------------------------------------------------------

def test_sort_stars_first():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1")
    quadrants = [i["quadrant"] for i in result["items"]]
    assert quadrants[0] == "star"
    assert quadrants[-1] in ("laggard", "question_mark")


# ---------------------------------------------------------------------------
# Empty input
# ---------------------------------------------------------------------------

def test_empty_margins_returns_empty_result():
    with _patch_margins([]):
        result = compute_menu_matrix("site_1")
    assert result["item_count"] == 0
    assert result["items"] == []
    for q in ("star", "cash_cow", "question_mark", "laggard"):
        assert result["quadrant_summary"][q]["count"] == 0


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def test_window_days_echoed():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1", days=14)
    assert result["window_days"] == 14


def test_item_count_correct():
    with _patch_margins(_FIXTURE_MARGINS):
        result = compute_menu_matrix("site_1")
    assert result["item_count"] == 4
