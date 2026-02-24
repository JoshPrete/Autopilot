from datetime import datetime, timezone

import pytest

from data.storage import store_xero_tokens
from data.xero import _evaluate_cost_guardrails, map_xero_lines_to_score_keys
from security.crypto import TokenEncryptionError


def test_mapping_proposal_is_not_applied_when_auto_apply_disabled(monkeypatch):
    monkeypatch.setattr("data.xero.settings.ALLOW_AUTO_APPLY_PROPOSED_MAPPINGS", False)
    monkeypatch.setattr("data.xero.settings.MIN_CONFIDENCE_AUTO_APPLY", 0.90)
    monkeypatch.setattr("data.xero.settings.ANTHROPIC_API_KEY", "")

    def _get_mapping(_site_id, _description, status=None):
        if status == "approved":
            return None
        if status == "proposed":
            return {
                "id": 10,
                "score_key": "oat_milk_ml",
                "confidence": 0.96,
                "units_per_pack": 1,
                "status": "proposed",
                "source": "llm",
            }
        return None

    captured = []
    monkeypatch.setattr("data.xero.get_xero_line_mapping", _get_mapping)
    monkeypatch.setattr(
        "data.xero.enqueue_xero_review_item", lambda **kwargs: captured.append(kwargs) or 1
    )

    result = map_xero_lines_to_score_keys(
        "site-1",
        [{"description": "Oat Milk", "unit_amount": 4.2, "quantity": 1}],
    )

    assert result["mapped_lines"] == []
    assert result["review_additions"]["PENDING_APPROVAL"] == 1
    assert captured[0]["reason_code"] == "PENDING_APPROVAL"


def test_mapping_proposal_can_auto_apply_when_enabled_and_high_confidence(monkeypatch):
    monkeypatch.setattr("data.xero.settings.ALLOW_AUTO_APPLY_PROPOSED_MAPPINGS", True)
    monkeypatch.setattr("data.xero.settings.MIN_CONFIDENCE_AUTO_APPLY", 0.90)
    monkeypatch.setattr("data.xero.settings.ANTHROPIC_API_KEY", "")
    monkeypatch.setattr("data.xero.enqueue_xero_review_item", lambda **_kwargs: 1)

    def _get_mapping(_site_id, _description, status=None):
        if status == "approved":
            return None
        if status == "proposed":
            return {
                "id": 12,
                "score_key": "oat_milk_ml",
                "confidence": 0.95,
                "units_per_pack": 1,
                "status": "proposed",
                "source": "llm",
            }
        return None

    monkeypatch.setattr("data.xero.get_xero_line_mapping", _get_mapping)

    result = map_xero_lines_to_score_keys(
        "site-1",
        [{"description": "Oat Milk", "unit_amount": 4.2, "quantity": 1}],
    )

    assert len(result["mapped_lines"]) == 1
    assert result["proposals_auto_applied"] == 1
    assert result["review_additions"] == {}


def test_cost_guardrail_blocks_excessive_delta(monkeypatch):
    monkeypatch.setattr("data.xero.settings.MAX_COST_DELTA_PCT", 40.0)
    monkeypatch.setattr("data.xero.get_recent_xero_cost_history", lambda *_a, **_k: [])

    allowed, reason, context = _evaluate_cost_guardrails(
        site_id="site-1",
        score_key="beans",
        proposed_cost_cents=200,
        current_cost_cents=100,
    )

    assert allowed is False
    assert reason == "EXCESSIVE_DELTA"
    assert context["delta_pct"] == 100.0


def test_cost_guardrail_blocks_iqr_outlier(monkeypatch):
    monkeypatch.setattr("data.xero.settings.MAX_COST_DELTA_PCT", 500.0)
    monkeypatch.setattr(
        "data.xero.get_recent_xero_cost_history", lambda *_a, **_k: [98, 100, 101, 99, 102]
    )

    allowed, reason, _context = _evaluate_cost_guardrails(
        site_id="site-1",
        score_key="beans",
        proposed_cost_cents=300,
        current_cost_cents=None,
    )

    assert allowed is False
    assert reason == "OUTLIER_COST"


def test_store_xero_tokens_requires_encryption_key(monkeypatch):
    monkeypatch.setattr("data.storage.token_encryption_ready", lambda: False)

    with pytest.raises(TokenEncryptionError):
        store_xero_tokens(
            site_id="site-1",
            tenant_id="tenant-1",
            access_token="access",
            refresh_token="refresh",
            expires_at=datetime.now(timezone.utc),
            scope="scope",
        )
