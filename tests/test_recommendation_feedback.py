from datetime import date

from app.routers.recommendations import submit_feedback
from app.schemas import RecommendationFeedbackRequest


def test_submit_feedback_success(monkeypatch):
    monkeypatch.setattr(
        "app.routers.recommendations.get_recommendation",
        lambda *_args, **_kwargs: {"rec_id": "rec-1"},
    )
    monkeypatch.setattr(
        "app.routers.recommendations.store_adoption_log",
        lambda **_kwargs: "log-1",
    )
    site = {"site_id": "site-1"}
    body = RecommendationFeedbackRequest(
        adopted=True,
        manager_name="Josh",
        helpfulness_rating=4,
        notes="Worked well",
        log_date=date(2026, 2, 18),
    )
    result = submit_feedback("rec-1", body, site=site)
    assert result["status"] == "ok"
    assert result["log_id"] == "log-1"
