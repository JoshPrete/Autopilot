"""Tests for role-based access restrictions (Phase 3).

Verifies the access matrix:
  - Financial/admin endpoints: 401 without auth, 403 for staff, 200 for manager
  - Operational endpoints: remain open (no auth required)
"""

import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests")

from app.auth import create_jwt
from app.main import app
from tests.conftest import TEST_SITE_ID, TEST_SITE_NAME

client = TestClient(app)

MOCK_SITE = {"site_id": TEST_SITE_ID, "name": TEST_SITE_NAME}


def _manager_token():
    return create_jwt(
        contact_id="00000000-0000-0000-0000-000000000020",
        site_id=TEST_SITE_ID,
        role="MANAGER",
        name="Josh Manager",
    )


def _staff_token():
    return create_jwt(
        contact_id="00000000-0000-0000-0000-000000000010",
        site_id=TEST_SITE_ID,
        role="P1",
        name="Sarah Barista",
    )


# Financial endpoints that require MANAGER role
LOCKED_GET_ENDPOINTS = [
    f"/api/sites/{TEST_SITE_ID}/analysis/bottom-line-scorecard",
    f"/api/sites/{TEST_SITE_ID}/analysis/weekly-roi",
    f"/api/sites/{TEST_SITE_ID}/analysis/weekly-review",
    f"/api/sites/{TEST_SITE_ID}/analysis/daily-efficiency",
    f"/api/sites/{TEST_SITE_ID}/analysis/optimized-shifts",
]


@patch("app.dependencies.get_site", return_value=MOCK_SITE)
def test_locked_endpoints_require_auth(mock_site):
    """Financial endpoints return 401 without any auth token."""
    for url in LOCKED_GET_ENDPOINTS:
        resp = client.get(url)
        assert resp.status_code == 401, f"{url} should be 401, got {resp.status_code}"


@patch("app.dependencies.get_site", return_value=MOCK_SITE)
def test_locked_endpoints_reject_staff(mock_site):
    """Financial endpoints return 403 for staff (P1/P2/P3)."""
    headers = {"Authorization": f"Bearer {_staff_token()}"}
    for url in LOCKED_GET_ENDPOINTS:
        resp = client.get(url, headers=headers)
        assert resp.status_code == 403, f"{url} should be 403 for staff, got {resp.status_code}"


@patch("app.routers.analysis.get_bottom_line_scorecard", return_value={"revenue": 100})
@patch("app.dependencies.get_site", return_value=MOCK_SITE)
def test_bottom_line_scorecard_manager_allowed(mock_site, mock_scorecard):
    """Managers can access bottom-line-scorecard."""
    headers = {"Authorization": f"Bearer {_manager_token()}"}
    resp = client.get(
        f"/api/sites/{TEST_SITE_ID}/analysis/bottom-line-scorecard",
        headers=headers,
    )
    assert resp.status_code == 200


@patch("app.dependencies.get_site", return_value=MOCK_SITE)
def test_delivery_send_requires_auth(mock_site):
    """POST /delivery/send returns 401 without auth."""
    resp = client.post(
        f"/api/sites/{TEST_SITE_ID}/delivery/send",
        json={"role": "P1", "message_body": "test"},
    )
    assert resp.status_code == 401


@patch("app.dependencies.get_site", return_value=MOCK_SITE)
def test_delivery_send_rejects_staff(mock_site):
    """POST /delivery/send returns 403 for staff."""
    headers = {"Authorization": f"Bearer {_staff_token()}"}
    resp = client.post(
        f"/api/sites/{TEST_SITE_ID}/delivery/send",
        json={"role": "P1", "message_body": "test"},
        headers=headers,
    )
    assert resp.status_code == 403


@patch("data.storage.get_site", return_value=MOCK_SITE)
def test_xero_connect_requires_auth(mock_site):
    """GET /xero/connect returns 401 without auth."""
    resp = client.get(f"/api/xero/connect?site_id={TEST_SITE_ID}")
    assert resp.status_code == 401


@patch("data.storage.get_site", return_value=MOCK_SITE)
def test_xero_connect_rejects_staff(mock_site):
    """GET /xero/connect returns 403 for staff."""
    headers = {"Authorization": f"Bearer {_staff_token()}"}
    resp = client.get(f"/api/xero/connect?site_id={TEST_SITE_ID}", headers=headers)
    assert resp.status_code == 403


@patch("app.routers.tomorrow_plan.get_prediction")
@patch("app.dependencies.get_site", return_value=MOCK_SITE)
def test_tomorrow_plan_open_without_auth(mock_site, mock_pred):
    """Tomorrow plan endpoints remain accessible without auth."""
    mock_pred.return_value = None  # 404 is fine, we just check it's not 401
    resp = client.get(
        f"/api/sites/{TEST_SITE_ID}/tomorrow-plan/json?target_date=2026-01-01"
    )
    # Should be 404 (no prediction), NOT 401 (auth required)
    assert resp.status_code == 404


@patch("app.routers.analysis.get_rolling_accuracy", return_value={"accuracy": 0.85})
@patch("app.dependencies.get_site", return_value=MOCK_SITE)
def test_accuracy_open_without_auth(mock_site, mock_accuracy):
    """Accuracy endpoint remains accessible without auth."""
    resp = client.get(f"/api/sites/{TEST_SITE_ID}/analysis/accuracy")
    assert resp.status_code == 200


@patch("app.routers.xero.consume_xero_oauth_state", return_value=None)
def test_xero_callback_open_without_auth(mock_consume):
    """Xero OAuth callback does not require auth (it's the return from Xero)."""
    # Will fail with 400 (invalid state) not 401 (auth required)
    resp = client.get("/api/xero/callback?code=fake&state=fake")
    assert resp.status_code != 401
    assert resp.status_code != 403
