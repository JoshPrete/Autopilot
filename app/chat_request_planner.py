from __future__ import annotations


def _keyword_match(question: str, keywords: list[str]) -> bool:
    q = (question or "").lower()
    return any(keyword in q for keyword in keywords)


def classify_chat_request(question: str) -> dict:
    q = (question or "").strip()
    lowered = q.lower()

    if _keyword_match(
        lowered,
        [
            "who's working tomorrow",
            "who is working tomorrow",
            "who is on tomorrow",
            "who's on tomorrow",
            "tomorrow roster",
            "who works tomorrow",
        ],
    ):
        return {
            "intent": "direct_lookup",
            "lookup_key": "tomorrow_roster",
            "label": "Tomorrow roster lookup",
            "sources": ["deputy_rosters"],
            "timeframe": "tomorrow",
        }

    if _keyword_match(
        lowered,
        [
            "overstaffed",
            "understaffed",
            "labor",
            "labour",
            "wage",
            "wages",
            "margin",
            "efficiency",
        ],
    ):
        return {
            "intent": "analytical",
            "label": "Operational analysis",
            "sources": ["daily_profitability", "square_orders", "deputy_rosters"],
        }

    if _keyword_match(
        lowered,
        [
            "biggest risk",
            "what should we focus on",
            "what should i focus on",
            "priority this week",
            "operational risk",
            "what matters most",
        ],
    ):
        return {
            "intent": "strategic",
            "label": "Strategic synthesis",
            "sources": [
                "square_orders",
                "daily_profitability",
                "deputy_rosters",
                "xero_financial_facts",
                "xero_cogs",
            ],
        }

    return {
        "intent": "general",
        "label": "General assistant reasoning",
        "sources": [],
    }
