"""
Clubhouse Autopilot - Xero OAuth2 Endpoints
Handles connect flow, callback, and status/sync triggers.
"""

import logging
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import RedirectResponse

from config.settings import settings
from data.storage import (
    consume_xero_oauth_state,
    get_all_xero_mappings,
    get_site,
    store_xero_oauth_state,
    get_xero_tokens,
    store_xero_tokens,
)

logger = logging.getLogger("autopilot.xero_router")

router = APIRouter(prefix="/api/xero", tags=["xero"])

XERO_AUTHORIZE_URL = "https://login.xero.com/identity/connect/authorize"
XERO_TOKEN_URL = "https://identity.xero.com/connect/token"
XERO_CONNECTIONS_URL = "https://api.xero.com/connections"

XERO_SCOPES = (
    "openid offline_access accounting.transactions.read "
    "payroll.employees.read payroll.payruns.read "
    "payroll.payslip.read payroll.settings.read payroll.timesheets.read"
)


@router.get("/connect")
def xero_connect(site_id: str = Query(..., description="Site UUID")):
    """Redirect to Xero authorization page to begin OAuth2 flow."""
    if not settings.XERO_CLIENT_ID:
        raise HTTPException(status_code=500, detail="XERO_CLIENT_ID not configured")

    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{site_id}' not found")

    state = secrets.token_urlsafe(32)
    store_xero_oauth_state(
        site_id=site_id,
        state=state,
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=15),
    )

    params = {
        "response_type": "code",
        "client_id": settings.XERO_CLIENT_ID,
        "redirect_uri": settings.XERO_REDIRECT_URI,
        "scope": XERO_SCOPES,
        "state": state,
    }

    auth_url = f"{XERO_AUTHORIZE_URL}?{urlencode(params)}"
    logger.info("Redirecting site %s to Xero OAuth: %s", site_id, auth_url)
    return RedirectResponse(url=auth_url)


@router.get("/callback")
def xero_callback(
    code: str = Query(...),
    state: str = Query(...),
):
    """Handle Xero OAuth2 callback: exchange code for tokens."""
    # Validate state
    site_id = consume_xero_oauth_state(state)
    if not site_id:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    if not settings.XERO_CLIENT_ID or not settings.XERO_CLIENT_SECRET:
        raise HTTPException(status_code=500, detail="Xero credentials not configured")

    # Exchange code for tokens
    try:
        resp = requests.post(
            XERO_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.XERO_REDIRECT_URI,
                "client_id": settings.XERO_CLIENT_ID,
                "client_secret": settings.XERO_CLIENT_SECRET,
            },
            timeout=30,
        )
        resp.raise_for_status()
        token_data = resp.json()
    except requests.RequestException as e:
        logger.error("Xero token exchange failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Token exchange failed: {e}")

    access_token = token_data["access_token"]
    refresh_token = token_data["refresh_token"]
    expires_in = token_data.get("expires_in", 1800)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    scope = token_data.get("scope", "")

    # Get tenant ID (Xero org) from connections endpoint
    try:
        conn_resp = requests.get(
            XERO_CONNECTIONS_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=30,
        )
        conn_resp.raise_for_status()
        connections = conn_resp.json()
    except requests.RequestException as e:
        logger.error("Xero connections fetch failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Failed to get Xero tenant: {e}")

    if not connections:
        raise HTTPException(status_code=400, detail="No Xero organisations found for this user")

    # Use the first connected tenant
    tenant_id = connections[0]["tenantId"]
    tenant_name = connections[0].get("tenantName", "Unknown")

    # Store tokens
    store_xero_tokens(
        site_id=site_id,
        tenant_id=tenant_id,
        access_token=access_token,
        refresh_token=refresh_token,
        expires_at=expires_at,
        scope=scope,
    )

    logger.info("Xero connected for site %s: tenant '%s' (%s)", site_id, tenant_name, tenant_id)

    return RedirectResponse(url=f"/xero/setup?site_id={site_id}&connected=true")


@router.get("/status")
def xero_status(site_id: str = Query(..., description="Site UUID")):
    """Return Xero connection status for a site."""
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{site_id}' not found")

    tokens = get_xero_tokens(site_id)
    if not tokens:
        return {"connected": False, "site_id": site_id}

    mappings = get_all_xero_mappings(site_id)
    confirmed = sum(1 for m in mappings if m.get("confidence") == "confirmed")

    return {
        "connected": True,
        "site_id": site_id,
        "tenant_id": tokens["tenant_id"],
        "scope": tokens.get("scope", ""),
        "connected_at": str(tokens.get("connected_at", "")),
        "last_refreshed": str(tokens.get("updated_at", "")),
        "mappings_total": len(mappings),
        "mappings_confirmed": confirmed,
    }


@router.post("/sync")
def xero_sync(site_id: str = Query(..., description="Site UUID")):
    """Manually trigger a Xero bill sync."""
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{site_id}' not found")

    from data.xero import XeroError, is_xero_configured, sync_xero_bills

    if not is_xero_configured(site_id):
        raise HTTPException(status_code=400, detail="Xero not connected for this site")

    try:
        result = sync_xero_bills(site_id)
        return {"status": "ok", **result}
    except XeroError as e:
        logger.error("Manual Xero sync failed: %s", e)
        raise HTTPException(status_code=502, detail=str(e))


@router.get("/mappings")
def xero_mappings(site_id: str = Query(..., description="Site UUID")):
    """Get all Xero line-item mappings for review."""
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{site_id}' not found")

    mappings = get_all_xero_mappings(site_id)
    return {"mappings": mappings, "total": len(mappings)}
