"""
Operator rules router — Knowledge layer REST API.

Endpoints:
    GET    /api/sites/{site_id}/rules            — list rules (filterable by status)
    POST   /api/sites/{site_id}/rules            — create a rule manually
    PUT    /api/sites/{site_id}/rules/{rule_id}/confirm  — confirm a proposed rule
    PUT    /api/sites/{site_id}/rules/{rule_id}/reject   — reject a proposed rule
    DELETE /api/sites/{site_id}/rules/{rule_id}          — soft-delete (active=False)
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from app.auth import get_current_user, require_role
from app.dependencies import get_validated_site
from data.storage import (
    confirm_operator_rule,
    create_operator_rule,
    list_operator_rules,
    reject_operator_rule,
)

router = APIRouter(prefix="/api/sites/{site_id}", tags=["rules"])


# ── Request schemas ────────────────────────────────────────────────────────────

class RuleCreateRequest(BaseModel):
    rule_type: str
    rule_name: str
    payload: dict
    source: str = "manual"
    confidence: Optional[float] = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/rules")
def get_rules(
    site_id: str,
    status: Optional[str] = Query(None, description="Filter by status: proposed, confirmed, rejected"),
    active_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=200),
    _site: dict = Depends(get_validated_site),
    user: dict = Depends(get_current_user),
):
    """List operator rules for a site."""
    statuses = [status] if status else None
    rules = list_operator_rules(site_id, statuses=statuses, active_only=active_only, limit=limit)
    return {"rules": rules, "count": len(rules)}


@router.post("/rules", status_code=201)
def add_rule(
    site_id: str,
    body: RuleCreateRequest,
    _site: dict = Depends(get_validated_site),
    user: dict = Depends(require_role("MANAGER", "P1")),
):
    """Manually create an operator rule (bypasses chat capture flow)."""
    rule = create_operator_rule(
        site_id=site_id,
        rule_type=body.rule_type,
        rule_name=body.rule_name,
        payload=body.payload,
        source=body.source,
        status="proposed",
        confidence=body.confidence,
        created_by=user.get("sub"),
    )
    if not rule:
        raise HTTPException(status_code=503, detail="Rule could not be saved")
    return rule


@router.put("/rules/{rule_id}/confirm")
def confirm_rule(
    site_id: str,
    rule_id: str,
    _site: dict = Depends(get_validated_site),
    user: dict = Depends(require_role("MANAGER", "P1")),
):
    """Confirm a proposed rule, making it active operating knowledge."""
    rule = confirm_operator_rule(
        site_id=site_id,
        rule_id=rule_id,
        confirmed_by=user.get("name") or user.get("sub"),
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found or already confirmed")
    return rule


@router.put("/rules/{rule_id}/reject")
def reject_rule(
    site_id: str,
    rule_id: str,
    _site: dict = Depends(get_validated_site),
    user: dict = Depends(require_role("MANAGER", "P1")),
):
    """Reject a proposed rule."""
    rule = reject_operator_rule(
        site_id=site_id,
        rule_id=rule_id,
        rejected_by=user.get("name") or user.get("sub"),
    )
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule
