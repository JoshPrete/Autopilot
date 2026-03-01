"""
Phone + PIN authentication with JWT tokens.

Uses the existing contacts table (with added pin_hash column).
Provides FastAPI dependencies for protecting routes.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from config.settings import settings

logger = logging.getLogger("autopilot.auth")

_bearer_scheme = HTTPBearer(auto_error=False)

# JWT configuration
_JWT_ALGORITHM = "HS256"
_JWT_EXPIRY_HOURS = 72


def hash_pin(pin: str) -> str:
    """Hash a PIN using bcrypt."""
    return bcrypt.hashpw(pin.encode(), bcrypt.gensalt()).decode()


def verify_pin(pin: str, pin_hash: str) -> bool:
    """Verify a PIN against its bcrypt hash."""
    return bcrypt.checkpw(pin.encode(), pin_hash.encode())


def create_jwt(
    contact_id: str,
    site_id: str,
    role: str,
    name: str,
) -> str:
    """Create a JWT with user claims."""
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(contact_id),
        "site_id": str(site_id),
        "role": role,
        "name": name,
        "exp": now + timedelta(hours=_JWT_EXPIRY_HOURS),
        "iat": now,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=_JWT_ALGORITHM)


def decode_jwt(token: str) -> dict:
    """Decode and validate a JWT. Raises HTTPException on failure."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[_JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expired",
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
        )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> dict:
    """FastAPI dependency: require a valid JWT and return the decoded claims."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
        )
    return decode_jwt(credentials.credentials)


def get_optional_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Optional[dict]:
    """FastAPI dependency: decode JWT if present, return None if absent."""
    if credentials is None:
        return None
    try:
        return decode_jwt(credentials.credentials)
    except HTTPException:
        return None


def require_role(*roles: str):
    """
    Factory that returns a FastAPI dependency requiring the user to have
    one of the specified roles.

    Usage:
        @router.get("/admin")
        def admin_endpoint(_user: dict = Depends(require_role("MANAGER"))):
            ...
    """

    def _dependency(user: dict = Depends(get_current_user)) -> dict:
        if user.get("role") not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requires role: {', '.join(roles)}",
            )
        return user

    return _dependency
