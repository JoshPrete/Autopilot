import json

import pytest

from app.chat import stream_chat_response
from app.operator_knowledge import parse_operator_rule_message, summarize_operator_rule


def test_parse_recipe_definition_into_structured_payload():
    proposal = parse_operator_rule_message(
        "12oz latte uses 1 cup, 1 lid, 20g beans, 280ml full cream milk"
    )

    assert proposal is not None
    assert proposal["rule_type"] == "recipe_definition"
    assert proposal["payload"]["trigger_item_name"] == "12oz latte"
    assert proposal["payload"]["sale_profile"]["family"] == "latte"
    assert proposal["payload"]["sale_profile"]["size_label"] == "12oz"
    assert proposal["payload"]["components"][2] == {
        "item_name": "beans",
        "quantity": 20.0,
        "unit": "g",
    }
    assert summarize_operator_rule(proposal).startswith("12oz latte: uses")


def test_parse_staffing_constraint_into_structured_payload():
    proposal = parse_operator_rule_message("Do not roster juniors alone on Saturday open")

    assert proposal is not None
    assert proposal["rule_type"] == "staffing_constraint"
    assert proposal["payload"] == {
        "day_of_week": "saturday",
        "daypart": "open",
        "min_staff": 2,
        "requires_senior": True,
        "disallow_role_alone": "junior",
    }
    assert summarize_operator_rule(proposal) == (
        "Saturday open: minimum 2 staff, senior coverage required, do not leave junior alone"
    )


async def _collect_stream_payloads(generator):
    payloads = []
    async for chunk in generator:
        if not chunk.startswith("data: "):
            continue
        payload = json.loads(chunk[len("data: ") :].strip())
        if payload.get("done"):
            continue
        payloads.append(payload)
    return payloads


@pytest.mark.asyncio
async def test_stream_chat_response_proposes_operator_rule_without_anthropic(monkeypatch):
    monkeypatch.setattr(
        "app.chat.create_operator_rule",
        lambda **_kwargs: {
            "rule_id": "rule-1",
            "rule_type": "delivery_schedule",
            "rule_name": "Milk delivery schedule",
            "payload": {"subject": "Milk", "days": ["monday", "wednesday", "friday"]},
            "status": "proposed",
        },
    )

    payloads = await _collect_stream_payloads(
        stream_chat_response(
            site_id="site-1",
            site_name="Clubhouse",
            messages=[{"role": "user", "content": "Milk delivery is Monday, Wednesday, Friday"}],
            document_ids=[],
        )
    )

    assert payloads
    assert "Proposed operating rule captured." in payloads[0]["content"]
    assert "Milk: delivery on Monday, Wednesday, Friday" in payloads[0]["content"]


@pytest.mark.asyncio
async def test_stream_chat_response_confirms_pending_operator_rule(monkeypatch):
    monkeypatch.setattr(
        "app.chat.get_pending_operator_rule",
        lambda *_args, **_kwargs: {
            "rule_id": "rule-1",
            "rule_type": "delivery_schedule",
            "rule_name": "Milk delivery schedule",
            "payload": {"subject": "Milk", "days": ["monday", "wednesday", "friday"]},
            "status": "proposed",
        },
    )
    monkeypatch.setattr(
        "app.chat.confirm_operator_rule",
        lambda *_args, **_kwargs: {
            "rule_id": "rule-1",
            "rule_type": "delivery_schedule",
            "rule_name": "Milk delivery schedule",
            "payload": {"subject": "Milk", "days": ["monday", "wednesday", "friday"]},
            "status": "confirmed",
        },
    )

    payloads = await _collect_stream_payloads(
        stream_chat_response(
            site_id="site-1",
            site_name="Clubhouse",
            messages=[{"role": "user", "content": "confirm"}],
            document_ids=[],
        )
    )

    assert payloads
    assert "Operating rule saved." in payloads[0]["content"]
    assert "future chat reasoning" in payloads[0]["content"]


@pytest.mark.asyncio
async def test_stream_chat_response_asks_curiosity_question_before_anthropic(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(
        "app.chat.gather_chat_context",
        lambda *_args, **_kwargs: {
            "curiosity_agenda": [
                {
                    "agenda_type": "knowledge_gap",
                    "priority": "high",
                    "title": "Missing stock recipe for 12oz latte",
                    "question": "What does 12oz latte consume from stock?",
                    "why_it_matters": "12oz latte is selling heavily but has no stock recipe.",
                    "decision_unlocked": "More accurate stock depletion and COGS attribution by product",
                    "source_gap_type": "missing_recipe",
                }
            ]
        },
    )

    payloads = await _collect_stream_payloads(
        stream_chat_response(
            site_id="site-1",
            site_name="Clubhouse",
            messages=[{"role": "user", "content": "How can we improve profitability?"}],
            document_ids=[],
        )
    )

    assert payloads
    assert (
        "One thing I should learn before I give you a confident recommendation:"
        in payloads[0]["content"]
    )
    assert "What does 12oz latte consume from stock?" in payloads[0]["content"]
    assert payloads[0]["curiosity"]["source_gap_type"] == "missing_recipe"


@pytest.mark.asyncio
async def test_stream_chat_response_returns_no_key_error_when_no_curiosity(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "")
    monkeypatch.setattr("app.chat.gather_chat_context", lambda *_args, **_kwargs: {})

    payloads = await _collect_stream_payloads(
        stream_chat_response(
            site_id="site-1",
            site_name="Clubhouse",
            messages=[{"role": "user", "content": "How is trade today?"}],
            document_ids=[],
        )
    )

    assert payloads
    assert payloads[0]["error"] == "ANTHROPIC_API_KEY not configured"


@pytest.mark.asyncio
async def test_stream_chat_response_can_ask_labor_explanation_question(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(
        "app.chat.gather_chat_context",
        lambda *_args, **_kwargs: {
            "curiosity_agenda": [
                {
                    "agenda_type": "labor_explanation",
                    "priority": "high",
                    "title": "Explain recent wage spike",
                    "question": (
                        "Wage % hit 38.5% on 2026-02-17. Was that training, sick cover, "
                        "public-holiday rates, soft trade, or an intentional roster choice?"
                    ),
                    "why_it_matters": "Wage % is still above target by $182/wk.",
                    "decision_unlocked": "Sharper labor diagnosis",
                }
            ]
        },
    )

    payloads = await _collect_stream_payloads(
        stream_chat_response(
            site_id="site-1",
            site_name="Clubhouse",
            messages=[{"role": "user", "content": "How can we improve profitability?"}],
            document_ids=[],
        )
    )

    assert payloads
    assert "Wage % hit 38.5% on 2026-02-17" in payloads[0]["content"]
    assert "Reply in plain language with the cause" in payloads[0]["content"]
    assert payloads[0]["curiosity"]["agenda_type"] == "labor_explanation"
