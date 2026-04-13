from data.storage import _build_xero_health_annotations


def test_build_xero_health_annotations_flags_missing_reports_scope_and_review_backlog():
    result = _build_xero_health_annotations(
        connected=True,
        scope="openid accounting.transactions.read offline_access",
        approved_mappings=0,
        proposed_mappings=14,
        review_counts={"PENDING_APPROVAL": 73, "UNMAPPED": 16, "LOW_CONFIDENCE": 5},
    )

    assert result["has_reports_scope"] is False
    assert result["review_queue_open"] == 94
    assert any("accounting.reports.read" in note for note in result["limitations"])
    assert any("No approved Xero bill mappings yet" in note for note in result["blockers"])
    assert any("94 open Xero review items" in note for note in result["blockers"])


def test_build_xero_health_annotations_stays_clean_when_scope_and_mappings_are_ready():
    result = _build_xero_health_annotations(
        connected=True,
        scope="openid accounting.transactions.read accounting.reports.read offline_access",
        approved_mappings=8,
        proposed_mappings=0,
        review_counts={},
    )

    assert result["has_reports_scope"] is True
    assert result["review_queue_open"] == 0
    assert result["blockers"] == []
    assert result["limitations"] == []
