"""
Auth endpoints: login, me, set-pin.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.auth import create_jwt, get_current_user, hash_pin, verify_pin
from data.storage import get_contact_by_phone, update_contact_pin

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    phone_e164: str
    pin: str


class SetPinRequest(BaseModel):
    phone_e164: str
    pin: str


@router.post("/login")
def login(body: LoginRequest):
    """Authenticate with phone + PIN and return a JWT."""
    contact = get_contact_by_phone(body.phone_e164)
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or PIN",
        )

    pin_hash = contact.get("pin_hash")
    if not pin_hash:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="PIN not set. Ask a manager to set your PIN.",
        )

    if not verify_pin(body.pin, pin_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone number or PIN",
        )

    token = create_jwt(
        contact_id=str(contact["contact_id"]),
        site_id=str(contact["site_id"]),
        role=contact["role_label"],
        name=contact["full_name"],
    )
    return {
        "access_token": token,
        "token_type": "bearer",
        "name": contact["full_name"],
        "role": contact["role_label"],
    }


@router.get("/me")
def me(user: dict = Depends(get_current_user)):
    """Return the current authenticated user's info from the JWT."""
    return {
        "contact_id": user["sub"],
        "site_id": user["site_id"],
        "role": user["role"],
        "name": user["name"],
    }


@router.post("/set-pin")
def set_pin(body: SetPinRequest, user: dict = Depends(get_current_user)):
    """Set or reset a user's PIN. Requires MANAGER role."""
    if user.get("role") != "MANAGER":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only managers can set PINs",
        )

    contact = get_contact_by_phone(body.phone_e164)
    if not contact:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No contact found for {body.phone_e164}",
        )

    hashed = hash_pin(body.pin)
    update_contact_pin(str(contact["contact_id"]), hashed)
    return {"status": "ok", "phone_e164": body.phone_e164}
