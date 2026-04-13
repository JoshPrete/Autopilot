"""
Menu engineering — popularity × margin matrix.

Classifies menu items into four quadrants based on median splits:
    star          — high popularity, high margin  → protect and promote
    cash_cow      — high popularity, lower margin → volume leader, thin margin
    question_mark — low popularity, high margin   → good margin, underperforming
    laggard       — low popularity, low margin    → review for removal

Uses compute_item_margins() for combined quantity + margin data, then
enriches each item with a sale profile from sale_understanding.infer_sale_profile().
"""

from __future__ import annotations

from analysis.profitability import compute_item_margins
from analysis.sale_understanding import infer_sale_profile


_QUADRANT_LABELS = {
    "star": "Star",
    "cash_cow": "Cash Cow",
    "question_mark": "Question Mark",
    "laggard": "Laggard",
}

_QUADRANT_RECOMMENDATIONS = {
    "star": "High volume and high margin — protect pricing and keep it prominent on the menu.",
    "cash_cow": "High volume but thin margin — consider a small price increase or review COGS.",
    "question_mark": "Good margin but low volume — try a promotion, bundle, or better placement.",
    "laggard": "Low volume and low margin — review whether to remove or rework this item.",
}


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    mid = n // 2
    if n % 2 == 1:
        return s[mid]
    return (s[mid - 1] + s[mid]) / 2.0


def compute_menu_matrix(site_id: str, days: int = 28) -> dict:
    """
    Return a popularity × margin matrix for all items sold in the last N days.

    Args:
        site_id: venue identifier
        days:    lookback window in days (default 28)

    Returns:
        {
            window_days, item_count, thresholds,
            items: [{item, score_key, category, qty, avg_price_cents, cogs_cents,
                     margin_pct, total_profit_cents, quadrant, sale_profile,
                     recommendation}],
            quadrant_summary: {star, cash_cow, question_mark, laggard}
        }
    """
    margins = compute_item_margins(site_id, days=days)

    if not margins:
        return {
            "window_days": days,
            "item_count": 0,
            "thresholds": {"popularity_median": 0, "margin_median": 0.0},
            "items": [],
            "quadrant_summary": {
                k: {"count": 0, "total_profit_cents": 0}
                for k in ("star", "cash_cow", "question_mark", "laggard")
            },
        }

    qtys = [float(r["qty"]) for r in margins]
    margin_pcts = [float(r["margin_pct"]) for r in margins]

    pop_median = _median(qtys)
    margin_median = _median(margin_pcts)

    quadrant_summary: dict[str, dict] = {
        k: {"count": 0, "total_profit_cents": 0}
        for k in ("star", "cash_cow", "question_mark", "laggard")
    }

    items = []
    for r in margins:
        qty = float(r["qty"])
        margin_pct = float(r["margin_pct"])

        high_pop = qty >= pop_median
        high_margin = margin_pct >= margin_median

        if high_pop and high_margin:
            quadrant = "star"
        elif high_pop and not high_margin:
            quadrant = "cash_cow"
        elif not high_pop and high_margin:
            quadrant = "question_mark"
        else:
            quadrant = "laggard"

        sale_profile = infer_sale_profile(r["item"])

        entry = {
            "item": r["item"],
            "score_key": r["score_key"],
            "category": r["category"],
            "qty": r["qty"],
            "avg_price_cents": r["avg_price_cents"],
            "cogs_cents": r["cogs_cents"],
            "cogs_source": r.get("cogs_source") or "unknown",
            "cogs_source_label": r.get("cogs_source_label") or "Cost basis",
            "cogs_detail": r.get("cogs_detail"),
            "cogs_components": r.get("cogs_components") or [],
            "margin_pct": r["margin_pct"],
            "total_profit_cents": r["total_profit_cents"],
            "quadrant": quadrant,
            "quadrant_label": _QUADRANT_LABELS[quadrant],
            "sale_profile": sale_profile,
            "recommendation": _QUADRANT_RECOMMENDATIONS[quadrant],
        }
        items.append(entry)

        qs = quadrant_summary[quadrant]
        qs["count"] += 1
        qs["total_profit_cents"] += r["total_profit_cents"]

    # Sort: stars first, then cash cows, question marks, laggards; within each by profit
    _order = {"star": 0, "cash_cow": 1, "question_mark": 2, "laggard": 3}
    items.sort(key=lambda x: (_order[x["quadrant"]], -x["total_profit_cents"]))

    return {
        "window_days": days,
        "item_count": len(items),
        "thresholds": {
            "popularity_median": pop_median,
            "margin_median": round(margin_median, 1),
        },
        "items": items,
        "quadrant_summary": quadrant_summary,
    }
