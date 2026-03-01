#!/usr/bin/env python3
"""
CLI tool to set a user's PIN for phone+PIN authentication.

Usage:
    python scripts/set_pin.py +61412345678 1234
"""

from __future__ import annotations

import sys
from pathlib import Path

# Ensure project root is on sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv

load_dotenv(PROJECT_ROOT / ".env")

from app.auth import hash_pin
from data.storage import get_contact_by_phone, update_contact_pin


def main() -> int:
    if len(sys.argv) != 3:
        print("Usage: python scripts/set_pin.py <phone_e164> <pin>")
        print("Example: python scripts/set_pin.py +61412345678 1234")
        return 1

    phone = sys.argv[1]
    pin = sys.argv[2]

    if not phone.startswith("+"):
        print(f"Error: Phone must be in E.164 format (e.g. +61412345678), got: {phone}")
        return 1

    if len(pin) < 4:
        print("Error: PIN must be at least 4 digits")
        return 1

    contact = get_contact_by_phone(phone)
    if not contact:
        print(f"Error: No active contact found for {phone}")
        return 1

    hashed = hash_pin(pin)
    update_contact_pin(str(contact["contact_id"]), hashed)

    print(f"PIN set for {contact['full_name']} ({contact['role_label']}) at {phone}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
