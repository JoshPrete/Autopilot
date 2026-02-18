"""
Clubhouse Autopilot - Xero Accounting Client
Pull supplier bills (ACCPAY) to auto-update COGS via item_costs.

Follows the same patterns as data/deputy.py:
  - Class-based client with authenticated requests
  - Fail-quiet when not configured
  - Normalized output ready for storage

Key difference: Xero uses OAuth2 with refresh tokens (not static bearer),
so tokens are stored in DB and auto-refreshed on expiry.
"""

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone

import anthropic
import requests

from config.settings import settings
from data.storage import (
    get_all_xero_mappings,
    get_item_costs,
    get_xero_line_mapping,
    get_xero_tokens,
    store_xero_line_mapping,
    update_xero_tokens,
    upsert_item_cost,
)

logger = logging.getLogger("autopilot.xero")

XERO_IDENTITY_URL = "https://identity.xero.com/connect/token"
XERO_API_BASE = "https://api.xero.com/api.xro/2.0"


class XeroError(Exception):
    """Raised when Xero API calls fail."""
    pass


class XeroClient:
    """
    Client for the Xero Accounting API.

    Requires OAuth2 tokens stored in xero_tokens table (via /api/xero/connect flow).
    Auto-refreshes expired tokens before each request.

    Usage:
        client = XeroClient(site_id)
        bills = client.fetch_bills(since_date=date(2026, 1, 1))
    """

    def __init__(self, site_id: str):
        self.site_id = site_id
        tokens = get_xero_tokens(site_id)
        if not tokens:
            raise XeroError("Xero not connected for this site. Visit /xero/setup to connect.")

        self.tenant_id = tokens["tenant_id"]
        self.access_token = tokens["access_token"]
        self.refresh_token = tokens["refresh_token"]
        self.expires_at = tokens["expires_at"]

        self.session = requests.Session()
        self._update_session_headers()

    def _update_session_headers(self):
        self.session.headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Xero-Tenant-Id": self.tenant_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def _ensure_valid_token(self):
        """Refresh the access token if it has expired or is about to."""
        now = datetime.now(timezone.utc)
        # Refresh if expiring within 60 seconds
        if self.expires_at.tzinfo is None:
            expires = self.expires_at.replace(tzinfo=timezone.utc)
        else:
            expires = self.expires_at

        if now < expires - timedelta(seconds=60):
            return  # Still valid

        logger.info("Xero token expired, refreshing...")

        if not settings.XERO_CLIENT_ID or not settings.XERO_CLIENT_SECRET:
            raise XeroError("XERO_CLIENT_ID and XERO_CLIENT_SECRET required for token refresh")

        try:
            resp = requests.post(
                XERO_IDENTITY_URL,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": self.refresh_token,
                    "client_id": settings.XERO_CLIENT_ID,
                    "client_secret": settings.XERO_CLIENT_SECRET,
                },
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise XeroError(f"Token refresh failed: {e}") from e

        self.access_token = data["access_token"]
        self.refresh_token = data["refresh_token"]
        self.expires_at = datetime.now(timezone.utc) + timedelta(seconds=data["expires_in"])

        # Persist new tokens
        update_xero_tokens(
            self.site_id,
            self.access_token,
            self.refresh_token,
            self.expires_at,
        )

        self._update_session_headers()
        logger.info("Xero token refreshed successfully")

    def _request(self, method: str, endpoint: str, **kwargs) -> dict:
        """Make an authenticated request to the Xero API."""
        self._ensure_valid_token()

        url = f"{XERO_API_BASE}/{endpoint.lstrip('/')}"
        try:
            resp = self.session.request(method, url, timeout=30, **kwargs)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            raise XeroError(f"Xero API error: {e}") from e

    def fetch_bills(self, since_date: date = None) -> list[dict]:
        """
        Fetch supplier bills (ACCPAY invoices) from Xero.

        The list endpoint returns summary data without line items,
        so we fetch each invoice individually to get full detail.

        Args:
            since_date: Only fetch bills modified since this date.

        Returns:
            List of normalized bill dicts with line items.
        """
        where_clause = 'Type=="ACCPAY"'
        params = {"where": where_clause, "order": "Date DESC"}

        if since_date:
            headers = {"If-Modified-Since": since_date.isoformat() + "T00:00:00"}
        else:
            headers = {}

        # 1. Get list of invoice IDs
        try:
            data = self._request(
                "GET", "Invoices",
                params=params,
                headers=headers,
            )
        except XeroError:
            raise

        raw_bills = data.get("Invoices", [])
        if not isinstance(raw_bills, list):
            logger.warning("Unexpected bills response type: %s", type(raw_bills))
            return []

        logger.info("Found %d bills, fetching line items...", len(raw_bills))

        # 2. Fetch each invoice individually for full line item detail
        # Xero rate limit: 60 calls/minute — pace requests ~1/sec
        bills = []
        for i, raw in enumerate(raw_bills):
            invoice_id = raw.get("InvoiceID")
            if not invoice_id:
                continue
            if i > 0:
                time.sleep(1.0)
            try:
                detail = self._request("GET", f"Invoices/{invoice_id}")
                full_invoice = detail.get("Invoices", [{}])[0]
                bills.append(self._normalize_bill(full_invoice))
            except (KeyError, TypeError, ValueError) as e:
                logger.warning(
                    "Skipping malformed bill %s: %s",
                    raw.get("InvoiceNumber", "?"), e,
                )
                continue
            except XeroError as e:
                logger.warning("Failed to fetch bill %s: %s", invoice_id, e)
                continue

        logger.info("Fetched %d bills with line items from Xero", len(bills))
        return bills

    def _normalize_bill(self, raw: dict) -> dict:
        """Convert a raw Xero invoice to our storage format."""
        line_items = []
        for li in raw.get("LineItems", []):
            description = (li.get("Description") or "").strip()
            if not description:
                continue
            line_items.append({
                "description": description,
                "unit_amount": li.get("UnitAmount", 0),
                "quantity": li.get("Quantity", 1),
                "line_amount": li.get("LineAmount", 0),
                "account_code": li.get("AccountCode"),
            })

        # Parse date: Xero returns "/Date(1234567890000+0000)/" format
        bill_date = None
        raw_date = raw.get("DateString") or raw.get("Date", "")
        if raw_date:
            try:
                bill_date = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).date()
            except (ValueError, AttributeError):
                pass

        supplier_name = ""
        contact = raw.get("Contact")
        if contact and isinstance(contact, dict):
            supplier_name = contact.get("Name", "")

        return {
            "invoice_number": raw.get("InvoiceNumber", ""),
            "supplier": supplier_name,
            "date": bill_date,
            "status": raw.get("Status", ""),
            "total": raw.get("Total", 0),
            "line_items": line_items,
        }


def is_xero_configured(site_id: str) -> bool:
    """Check if Xero tokens exist for a site (used for fail-quiet checks)."""
    tokens = get_xero_tokens(site_id)
    return tokens is not None


# ============================================================
# LLM Line-Item Mapping
# ============================================================


def map_xero_lines_to_score_keys(
    site_id: str,
    line_items: list[dict],
) -> list[dict]:
    """
    Map Xero line-item descriptions to score_keys using cached mappings
    and Claude LLM for unmapped items.

    Args:
        site_id: Site UUID
        line_items: List of dicts with 'description', 'unit_amount', 'quantity'

    Returns:
        List of dicts with 'description', 'score_key', 'category',
        'unit_cost_cents', 'confidence'
    """
    # Deduplicate descriptions
    unique_descriptions = list({li["description"] for li in line_items})

    # Check cache first
    cached = {}
    uncached = []
    for desc in unique_descriptions:
        score_key = get_xero_line_mapping(site_id, desc)
        if score_key:
            cached[desc] = score_key
        else:
            uncached.append(desc)

    logger.info(
        "Xero line mapping: %d cached, %d need LLM mapping",
        len(cached), len(uncached),
    )

    # LLM mapping for uncached items (batch in groups of 25 to avoid token limits)
    llm_mapped = {}
    if uncached and settings.ANTHROPIC_API_KEY:
        batch_size = 25
        for i in range(0, len(uncached), batch_size):
            batch = uncached[i:i + batch_size]
            batch_result = _llm_map_descriptions(site_id, batch)
            llm_mapped.update(batch_result)
        # Cache new mappings
        for desc, mapping in llm_mapped.items():
            store_xero_line_mapping(
                site_id, desc, mapping["score_key"], mapping.get("confidence", "unconfirmed"),
            )

    # Build result list — join back to line items
    results = []
    for li in line_items:
        desc = li["description"]
        score_key = cached.get(desc)
        category = "drink"  # default
        confidence = "confirmed" if score_key else "unconfirmed"

        if not score_key and desc in llm_mapped:
            mapping = llm_mapped[desc]
            score_key = mapping["score_key"]
            category = mapping.get("category", "drink")
            confidence = mapping.get("confidence", "unconfirmed")

        if not score_key:
            # Skip items that couldn't be mapped
            logger.debug("Could not map Xero line: '%s'", desc)
            continue

        unit_cost_cents = int(round(float(li.get("unit_amount", 0)) * 100))

        results.append({
            "description": desc,
            "score_key": score_key,
            "category": category,
            "unit_cost_cents": unit_cost_cents,
            "confidence": confidence,
        })

    return results


def _llm_map_descriptions(site_id: str, descriptions: list[str]) -> dict:
    """
    Use Claude to map Xero line descriptions to score_keys.

    Returns: {description: {score_key, category, confidence}}
    """
    # Get existing score_keys for context
    existing_costs = get_item_costs(site_id)
    existing_keys = list(existing_costs.keys())

    prompt = f"""You are mapping supplier invoice line items to menu item score_keys for a cafe.

Existing score_keys in the system:
{json.dumps(existing_keys, indent=2)}

Map each supplier line description to the most appropriate score_key.
- If it clearly matches an existing key, use that key
- If it's a new ingredient, create a snake_case key (e.g. "oat_milk", "vanilla_syrup")
- Category should be one of: "drink", "food", "retail", "ingredient"
- Confidence: "high" if clear match, "medium" if reasonable guess, "low" if uncertain
- Skip items that are clearly not menu-related (e.g. "cleaning supplies", "rent")

Supplier line descriptions to map:
{json.dumps(descriptions, indent=2)}

Respond with ONLY valid JSON — an array of objects:
[{{"description": "...", "score_key": "...", "category": "...", "confidence": "..."}}]

If an item should be skipped (not food/drink related), omit it from the response."""

    try:
        client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)
        response = client.messages.create(
            model="claude-sonnet-4-5-20250929",
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )

        text = response.content[0].text.strip()

        # Extract JSON from response (handle markdown code blocks)
        if text.startswith("```"):
            lines = text.split("\n")
            json_lines = []
            in_block = False
            for line in lines:
                if line.startswith("```"):
                    in_block = not in_block
                    continue
                if in_block:
                    json_lines.append(line)
            text = "\n".join(json_lines)

        mappings_list = json.loads(text)

        result = {}
        for m in mappings_list:
            desc = m.get("description", "")
            if desc and m.get("score_key"):
                result[desc] = {
                    "score_key": m["score_key"],
                    "category": m.get("category", "ingredient"),
                    "confidence": m.get("confidence", "medium"),
                }

        logger.info("LLM mapped %d/%d Xero descriptions", len(result), len(descriptions))
        return result

    except Exception as e:
        logger.warning("LLM mapping failed (non-fatal): %s", e)
        return {}


# ============================================================
# Sync Pipeline
# ============================================================


def sync_xero_bills(site_id: str, days_back: int = 30) -> dict:
    """
    Full sync: fetch bills -> map lines -> update item_costs.
    Called by daily scheduler and manual trigger.

    Returns:
        Summary dict: {bills_fetched, items_mapped, costs_updated}
    """
    since_date = date.today() - timedelta(days=days_back)

    # 1. Fetch bills
    client = XeroClient(site_id)
    bills = client.fetch_bills(since_date=since_date)
    if not bills:
        logger.info("No Xero bills found since %s", since_date)
        return {"bills_fetched": 0, "items_mapped": 0, "costs_updated": 0}

    # 2. Collect all line items
    all_lines = []
    for bill in bills:
        for li in bill.get("line_items", []):
            all_lines.append(li)

    if not all_lines:
        logger.info("No line items in %d bills", len(bills))
        return {"bills_fetched": len(bills), "items_mapped": 0, "costs_updated": 0}

    # 3. Map line descriptions to score_keys
    mapped = map_xero_lines_to_score_keys(site_id, all_lines)

    # 4. Update item_costs for each mapped line
    costs_updated = 0
    for item in mapped:
        upsert_item_cost(
            site_id=site_id,
            score_key=item["score_key"],
            category=item["category"],
            cost_cents=item["unit_cost_cents"],
            description=item["description"],
            source="xero",
        )
        costs_updated += 1

    logger.info(
        "Xero sync complete: %d bills, %d lines, %d mapped, %d costs updated",
        len(bills), len(all_lines), len(mapped), costs_updated,
    )

    return {
        "bills_fetched": len(bills),
        "items_mapped": len(mapped),
        "costs_updated": costs_updated,
    }
