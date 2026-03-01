"""Tests for Phone + PIN auth with JWT."""

import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi.testclient import TestClient

# Ensure JWT_SECRET is set before importing app
os.environ.setdefault("JWT_SECRET", "test-secret-key-for-unit-tests")

from app.auth import create_jwt, decode_jwt, hash_pin, verify_pin
from app.main import app
from config.settings import settings
from tests.conftest import TEST_SITE_ID

client = TestClient(app)

TEST_CONTACT = {
    "contact_id": "00000000-0000-0000-0000-000000000010",
    "site_id": TEST_SITE_ID,
    "full_name": "Sarah Barista",
    "phone_e164": "+61412345678",
    "role_label": "P1",
    "pin_hash": None,  # Will be set per test
}

MANAGER_CONTACT = {
    "contact_id": "00000000-0000-0000-0000-000000000020",
    "site_id": TEST_SITE_ID,
    "full_name": "Josh Manager",
    "phone_e164": "+61400000001",
    "role_label": "MANAGER",
    "pin_hash": None,
}


# ============================================================
# Unit tests: hash/verify/JWT
# ============================================================


def test_hash_and_verify_pin():
    """PIN hashing and verification round-trips correctly."""
    hashed = hash_pin("1234")
    assert verify_pin("1234", hashed)
    assert not verify_pin("0000", hashed)


def test_create_and_decode_jwt():
    """JWT creation and decoding round-trips correctly."""
    token = create_jwt(
        contact_id="abc-123",
        site_id="site-456",
        role="P1",
        name="Test User",
    )
    claims = decode_jwt(token)
    assert claims["sub"] == "abc-123"
    assert claims["site_id"] == "site-456"
    assert claims["role"] == "P1"
    assert claims["name"] == "Test User"
    assert "exp" in claims
    assert "iat" in claims


def test_decode_expired_jwt():
    """Expired JWT raises 401."""
    payload = {
        "sub": "abc",
        "site_id": "site",
        "role": "P1",
        "name": "Test",
        "exp": datetime.now(timezone.utc) - timedelta(hours=1),
        "iat": datetime.now(timezone.utc) - timedelta(hours=2),
    }
    token = jwt.encode(payload, settings.JWT_SECRET, algorithm="HS256")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        decode_jwt(token)
    assert exc_info.value.status_code == 401


def test_decode_invalid_jwt():
    """Garbage token raises 401."""
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        decode_jwt("not.a.real.token")
    assert exc_info.value.status_code == 401


def test_jwt_contains_72h_expiry():
    """JWT expires ~72 hours from creation."""
    token = create_jwt("id", "site", "P1", "Name")
    claims = decode_jwt(token)
    exp = datetime.fromtimestamp(claims["exp"], tz=timezone.utc)
    iat = datetime.fromtimestamp(claims["iat"], tz=timezone.utc)
    delta = exp - iat
    assert 71.9 < delta.total_seconds() / 3600 < 72.1


# ============================================================
# Integration tests: login endpoint
# ============================================================


@patch("app.routers.auth.get_contact_by_phone")
def test_login_success(mock_get_contact):
    """Valid phone + PIN returns JWT."""
    contact = {**TEST_CONTACT, "pin_hash": hash_pin("1234")}
    mock_get_contact.return_value = contact

    resp = client.post(
        "/api/auth/login",
        json={"phone_e164": "+61412345678", "pin": "1234"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert data["name"] == "Sarah Barista"
    assert data["role"] == "P1"


@patch("app.routers.auth.get_contact_by_phone")
def test_login_wrong_pin(mock_get_contact):
    """Wrong PIN returns 401."""
    contact = {**TEST_CONTACT, "pin_hash": hash_pin("1234")}
    mock_get_contact.return_value = contact

    resp = client.post(
        "/api/auth/login",
        json={"phone_e164": "+61412345678", "pin": "9999"},
    )
    assert resp.status_code == 401


@patch("app.routers.auth.get_contact_by_phone")
def test_login_unknown_phone(mock_get_contact):
    """Unknown phone returns 401."""
    mock_get_contact.return_value = None

    resp = client.post(
        "/api/auth/login",
        json={"phone_e164": "+61499999999", "pin": "1234"},
    )
    assert resp.status_code == 401


@patch("app.routers.auth.get_contact_by_phone")
def test_login_no_pin_set(mock_get_contact):
    """Contact without pin_hash returns 401 with helpful message."""
    contact = {**TEST_CONTACT, "pin_hash": None}
    mock_get_contact.return_value = contact

    resp = client.post(
        "/api/auth/login",
        json={"phone_e164": "+61412345678", "pin": "1234"},
    )
    assert resp.status_code == 401
    assert "PIN not set" in resp.json()["detail"]


# ============================================================
# Integration tests: /me endpoint
# ============================================================


def test_me_with_valid_token():
    """GET /me with valid Bearer token returns user info."""
    token = create_jwt(
        contact_id="abc-123",
        site_id=TEST_SITE_ID,
        role="MANAGER",
        name="Josh Manager",
    )
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["contact_id"] == "abc-123"
    assert data["role"] == "MANAGER"


def test_me_without_token():
    """GET /me without auth returns 401."""
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


# ============================================================
# Integration tests: /set-pin endpoint
# ============================================================


@patch("app.routers.auth.update_contact_pin")
@patch("app.routers.auth.get_contact_by_phone")
def test_set_pin_as_manager(mock_get_contact, mock_update_pin):
    """Managers can set PINs for contacts."""
    mock_get_contact.return_value = TEST_CONTACT

    token = create_jwt(
        contact_id=MANAGER_CONTACT["contact_id"],
        site_id=TEST_SITE_ID,
        role="MANAGER",
        name="Josh Manager",
    )
    resp = client.post(
        "/api/auth/set-pin",
        json={"phone_e164": "+61412345678", "pin": "5678"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200
    mock_update_pin.assert_called_once()


def test_set_pin_as_staff_forbidden():
    """Staff cannot set PINs."""
    token = create_jwt(
        contact_id=TEST_CONTACT["contact_id"],
        site_id=TEST_SITE_ID,
        role="P1",
        name="Sarah Barista",
    )
    resp = client.post(
        "/api/auth/set-pin",
        json={"phone_e164": "+61412345678", "pin": "5678"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403
