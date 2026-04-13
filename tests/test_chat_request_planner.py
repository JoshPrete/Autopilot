from app.chat_request_planner import classify_chat_request


def test_classify_chat_request_detects_tomorrow_roster_lookup():
    plan = classify_chat_request("Who's working tomorrow?")

    assert plan["intent"] == "direct_lookup"
    assert plan["lookup_key"] == "tomorrow_roster"
    assert plan["sources"] == ["deputy_rosters"]


def test_classify_chat_request_detects_analytical_question():
    plan = classify_chat_request("Are we overstaffed tomorrow?")

    assert plan["intent"] == "analytical"
    assert "deputy_rosters" in plan["sources"]
    assert "daily_profitability" in plan["sources"]


def test_classify_chat_request_detects_strategic_question():
    plan = classify_chat_request("What's the biggest operational risk this week?")

    assert plan["intent"] == "strategic"
    assert "xero_financial_facts" in plan["sources"]
