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
