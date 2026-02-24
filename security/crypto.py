"""Field-level token encryption helpers."""

from __future__ import annotations

from typing import Optional

from config.settings import settings

try:  # pragma: no cover - import guard for environments without dependency installed
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore[assignment]
    InvalidToken = Exception  # type: ignore[assignment]


TOKEN_PREFIX = "enc::"


class TokenEncryptionError(RuntimeError):
    """Raised when token encryption/decryption cannot be performed safely."""


def token_encryption_ready() -> bool:
    """Return True only when encryption dependency and key are both configured."""
    return bool(Fernet is not None and settings.AUTOPILOT_TOKEN_ENC_KEY)


def is_encrypted_secret(value: Optional[str]) -> bool:
    return isinstance(value, str) and value.startswith(TOKEN_PREFIX)


def _fernet() -> "Fernet":
    if Fernet is None:
        raise TokenEncryptionError(
            "Xero token encryption unavailable: install 'cryptography' dependency."
        )
    key = (settings.AUTOPILOT_TOKEN_ENC_KEY or "").strip()
    if not key:
        raise TokenEncryptionError(
            "AUTOPILOT_TOKEN_ENC_KEY missing; Xero sync is disabled until configured."
        )
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as e:
        raise TokenEncryptionError(
            "Invalid AUTOPILOT_TOKEN_ENC_KEY. Generate with Fernet.generate_key()."
        ) from e


def encrypt_secret(value: Optional[str]) -> str:
    if value is None:
        return ""
    if is_encrypted_secret(value):
        return value
    encrypted = _fernet().encrypt(value.encode("utf-8")).decode("utf-8")
    return f"{TOKEN_PREFIX}{encrypted}"


def decrypt_secret(value: Optional[str]) -> str:
    if value is None:
        return ""
    if not is_encrypted_secret(value):
        return value
    token = value[len(TOKEN_PREFIX) :]
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise TokenEncryptionError("Failed to decrypt Xero token (invalid key/token).") from e
