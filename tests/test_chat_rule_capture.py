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
async def test_stream_chat_response_appends_curiosity_follow_up_after_draft(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.chat._generate_draft_answer",
        lambda *_args, **_kwargs: "Cut Tuesday afternoon labor by one hour and protect the Saturday open.",
    )
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
        "Cut Tuesday afternoon labor by one hour and protect the Saturday open."
        in payloads[0]["content"]
    )
    assert "To improve this further, I need:" not in payloads[0]["content"]
    assert payloads[0]["follow_up_questions"] == ["What does 12oz latte consume from stock?"]
    assert payloads[0]["follow_up_hint"] == (
        "Reply in a recipe form like `12oz latte uses 1 cup, 1 lid, 20g beans, "
        "280ml full cream milk`."
    )
    assert payloads[0]["blocked"] is False
    assert any(
        rule["category"] == "missing_data_follow_up" for rule in payloads[0]["applied_rules"]
    )


@pytest.mark.asyncio
async def test_stream_chat_response_does_not_repeat_same_curiosity_question(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.chat._generate_draft_answer",
        lambda *_args, **_kwargs: "Tighten afternoon coverage and review cup usage on iced drinks.",
    )
    monkeypatch.setattr(
        "app.chat.gather_chat_context",
        lambda *_args, **_kwargs: {
            "curiosity_agenda": [
                {
                    "agenda_type": "knowledge_gap",
                    "priority": "high",
                    "title": "Missing stock recipe for 12oz/ICED SMALL",
                    "question": "What does 12oz/ICED SMALL consume from stock?",
                    "why_it_matters": (
                        "12oz/ICED SMALL sold 393 times in the last 30 days, but has no"
                        " confirmed recipe or usage rule."
                    ),
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
            messages=[
                {
                    "role": "assistant",
                    "content": (
                        "One thing I should learn before I give you a confident recommendation:\n\n"
                        "- Question: What does 12oz/ICED SMALL consume from stock?"
                    ),
                },
                {"role": "user", "content": "What should we focus on next?"},
            ],
            document_ids=[],
        )
    )

    assert payloads
    assert payloads[0]["draft_answer"] == (
        "Tighten afternoon coverage and review cup usage on iced drinks."
    )
    assert payloads[0]["follow_up_questions"] == []
    assert "To improve this further, I need:" not in payloads[0]["content"]


@pytest.mark.asyncio
async def test_stream_chat_response_does_not_repeat_same_structured_curiosity_question(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.chat._generate_draft_answer",
        lambda *_args, **_kwargs: "Keep the lunch rush covered and review cup usage after service.",
    )
    monkeypatch.setattr(
        "app.chat.gather_chat_context",
        lambda *_args, **_kwargs: {
            "curiosity_agenda": [
                {
                    "agenda_type": "knowledge_gap",
                    "priority": "high",
                    "title": "Missing stock recipe for 12oz/ICED SMALL",
                    "question": "What does 12oz/ICED SMALL consume from stock?",
                    "source_gap_type": "missing_recipe",
                }
            ]
        },
    )

    payloads = await _collect_stream_payloads(
        stream_chat_response(
            site_id="site-1",
            site_name="Clubhouse",
            messages=[
                {
                    "role": "assistant",
                    "content": "Based on the latest available data, here's my best answer.",
                    "follow_up_questions": ["What does 12oz/ICED SMALL consume from stock?"],
                },
                {"role": "user", "content": "What should we focus on next?"},
            ],
            document_ids=[],
        )
    )

    assert payloads
    assert payloads[0]["follow_up_questions"] == []


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
    assert payloads[0]["blocked"] is True
    assert payloads[0]["block_reason"] == "llm_unavailable"


@pytest.mark.asyncio
async def test_stream_chat_response_warns_on_stale_current_trade_question(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.chat._generate_draft_answer",
        lambda *_args, **_kwargs: "Trade looks softer than normal, so keep labor tight until the lunch window.",
    )
    monkeypatch.setattr(
        "app.chat.gather_chat_context",
        lambda *_args, **_kwargs: {
            "data_freshness": "2026-03-07",
            "data_health": {
                "status": "red",
                "components": [
                    {
                        "source": "square_orders",
                        "status": "red",
                        "latest_date": "2026-03-07",
                        "age_days": 9,
                    },
                    {
                        "source": "daily_profitability",
                        "status": "red",
                        "latest_date": "2026-03-07",
                        "age_days": 9,
                    },
                ],
            },
        },
    )

    payloads = await _collect_stream_payloads(
        stream_chat_response(
            site_id="site-1",
            site_name="Clubhouse",
            messages=[{"role": "user", "content": "How is trade today?"}],
            document_ids=[],
        )
    )

    assert payloads
    assert payloads[0]["blocked"] is False
    assert (
        "Trade looks softer than normal, so keep labor tight until the lunch window."
        in payloads[0]["content"]
    )
    assert "Confidence note:" not in payloads[0]["content"]
    assert "Square orders latest 07/03/2026 (9 days old)." in payloads[0]["warnings"]
    assert any(rule["category"] == "soft_warning" for rule in payloads[0]["applied_rules"])


@pytest.mark.asyncio
async def test_stream_chat_response_can_ask_labor_explanation_question(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.chat._generate_draft_answer",
        lambda *_args, **_kwargs: "Labor is the main drag on margin this week, so reduce low-output hours before changing price.",
    )
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
    assert (
        "Labor is the main drag on margin this week, so reduce low-output hours before changing price."
        in payloads[0]["content"]
    )
    assert "To improve this further, I need:" not in payloads[0]["content"]
    assert payloads[0]["follow_up_questions"] == [
        "Wage % hit 38.5% on 2026-02-17. Was that training, sick cover, public-holiday rates, soft trade, or an intentional roster choice?"
    ]
    assert any(
        rule["agenda_type"] == "labor_explanation" for rule in payloads[0]["applied_rules"]
    )


@pytest.mark.asyncio
async def test_stream_chat_response_blocks_on_active_partial_ingest(monkeypatch):
    monkeypatch.setattr("app.chat.settings.ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setattr(
        "app.chat._generate_draft_answer",
        lambda *_args, **_kwargs: "Sales are down versus last week, but I would confirm the ingest before acting.",
    )
    monkeypatch.setattr(
        "app.chat.gather_chat_context",
        lambda *_args, **_kwargs: {
            "data_health": {
                "status": "red",
                "components": [
                    {
                        "source": "square_orders",
                        "status": "green",
                        "latest_date": "2026-03-16",
                        "age_days": 0,
                    },
                    {
                        "source": "data_quality_flags",
                        "status": "red",
                        "active_flags": [
                            {
                                "flag_type": "partial_ingest",
                                "severity": "high",
                                "reason": "today only has 3 completed orders versus expected 140+",
                            }
                        ],
                    },
                ],
            }
        },
    )

    payloads = await _collect_stream_payloads(
        stream_chat_response(
            site_id="site-1",
            site_name="Clubhouse",
            messages=[{"role": "user", "content": "How is trade today?"}],
            document_ids=[],
        )
    )

    assert payloads
    assert payloads[0]["blocked"] is True
    assert payloads[0]["block_reason"] == "data_quality_block"
    assert "active data-quality flags indicate the underlying numbers may be incomplete" in payloads[0]["content"]


@pytest.mark.asyncio
async def test_stream_chat_response_handles_db_permission_failure_gracefully(monkeypatch):
    """When create_operator_rule returns None (DB unavailable / missing permissions),
    the chat should surface an actionable failure message rather than silently accepting."""
    monkeypatch.setattr(
        "app.chat.create_operator_rule",
        lambda **_kwargs: None,
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
    content = payloads[0]["content"]
    # Should surface a meaningful failure message, not silently accept
    assert any(
        phrase in content
        for phrase in [
            "could not persist",
            "operator_rules",
            "database",
            "could not save",
            "unable to save",
        ]
    ), f"Expected DB failure message, got: {content}"
