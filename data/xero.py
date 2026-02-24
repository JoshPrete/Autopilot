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
import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import anthropic
import requests

from config.settings import settings
from data.storage import (
    enqueue_xero_review_item,
    get_inventory_item_by_score_key,
    get_item_costs,
    get_recent_xero_cost_history,
    get_xero_line_mapping,
    list_inventory_items,
    resolve_xero_review_items_for_mapping,
    store_xero_cost_history,
    store_inventory_receipt,
    get_xero_tokens,
    upsert_xero_line_mapping,
    upsert_xero_financial_fact,
    update_xero_tokens,
    upsert_item_cost,
)

logger = logging.getLogger("autopilot.xero")

XERO_IDENTITY_URL = "https://identity.xero.com/connect/token"
XERO_API_BASE = "https://api.xero.com/api.xro/2.0"
XERO_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
XERO_MAX_RETRIES = 5
XERO_MAX_RETRY_DELAY_SECONDS = 20.0


class XeroError(Exception):
    """Raised when Xero API calls fail."""

    pass


class XeroAuthError(XeroError):
    """Raised when Xero auth or token refresh fails and requires reauthorization."""

    pass


def _parse_xero_date(raw_date) -> Optional[date]:
    """Parse common Xero date formats into a date."""
    if not raw_date:
        return None

    if isinstance(raw_date, datetime):
        return raw_date.date()

    if isinstance(raw_date, date):
        return raw_date

    value = str(raw_date)

    # Legacy format: /Date(1739577600000+0000)/
    if value.startswith("/Date("):
        match = re.search(r"/Date\((\d+)", value)
        if match:
            ts_ms = int(match.group(1))
            return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date()

    # ISO date or datetime string.
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        pass

    return None


def _retry_delay_seconds(attempt: int, retry_after_header: Optional[str] = None) -> float:
    """
    Compute bounded retry delay, honoring Retry-After when available.
    """
    if retry_after_header:
        try:
            value = float(retry_after_header)
            return max(1.0, min(value, XERO_MAX_RETRY_DELAY_SECONDS))
        except (TypeError, ValueError):
            pass

    # Exponential backoff: 1s, 2s, 4s, 8s, ...
    return min(2 ** max(0, attempt - 1), XERO_MAX_RETRY_DELAY_SECONDS)


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
        self.session.headers.update(
            {
                "Authorization": f"Bearer {self.access_token}",
                "Xero-Tenant-Id": self.tenant_id,
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

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
            raise XeroAuthError("XERO_CLIENT_ID and XERO_CLIENT_SECRET required for token refresh")

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
            if resp.status_code in (400, 401, 403):
                body = (resp.text or "").strip()[:500]
                raise XeroAuthError(
                    f"Xero token refresh unauthorized ({resp.status_code}). "
                    f"Reauthorize at /xero/setup. {body}"
                )
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            raise XeroAuthError(f"Token refresh failed: {e}. Reauthorize at /xero/setup.") from e

        if not data.get("access_token") or not data.get("refresh_token"):
            raise XeroAuthError(
                "Xero token refresh response missing rotated token(s). Reauthorize at /xero/setup."
            )

        self.access_token = data["access_token"]
        # Xero rotates refresh tokens. Always persist the NEW refresh token.
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
        last_error = None

        for attempt in range(1, XERO_MAX_RETRIES + 1):
            try:
                resp = self.session.request(method, url, timeout=30, **kwargs)
            except requests.RequestException as e:
                last_error = e
                if attempt >= XERO_MAX_RETRIES:
                    break
                delay = _retry_delay_seconds(attempt)
                logger.warning(
                    "Xero request network error (%s %s) attempt %d/%d: %s; retrying in %.1fs",
                    method,
                    endpoint,
                    attempt,
                    XERO_MAX_RETRIES,
                    e,
                    delay,
                )
                time.sleep(delay)
                continue

            status_code = int(resp.status_code)
            if status_code in (401, 403):
                body = (resp.text or "").strip()[:500]
                raise XeroAuthError(
                    f"Xero auth failed ({status_code}) for {endpoint}. "
                    f"Reauthorize at /xero/setup. {body}"
                )
            if status_code in XERO_RETRYABLE_STATUS_CODES and attempt < XERO_MAX_RETRIES:
                delay = _retry_delay_seconds(attempt, resp.headers.get("Retry-After"))
                logger.warning(
                    "Xero API retryable status (%s %s) attempt %d/%d: %s; retrying in %.1fs",
                    method,
                    endpoint,
                    attempt,
                    XERO_MAX_RETRIES,
                    status_code,
                    delay,
                )
                time.sleep(delay)
                continue

            try:
                resp.raise_for_status()
            except requests.RequestException as e:
                body = (resp.text or "").strip()
                if body:
                    body = body[:500]
                    raise XeroError(f"Xero API error ({status_code}): {body}") from e
                raise XeroError(f"Xero API error: {e}") from e

            try:
                return resp.json()
            except ValueError as e:
                raise XeroError(f"Xero API returned non-JSON response for {endpoint}") from e

        if last_error is not None:
            raise XeroError(
                f"Xero API error after {XERO_MAX_RETRIES} attempts: {last_error}"
            ) from last_error
        raise XeroError(f"Xero API error after {XERO_MAX_RETRIES} attempts for {endpoint}")

    def fetch_bank_transactions(self, since_date: date = None) -> list[dict]:
        """
        Fetch bank transactions and normalize daily in/out cash movement inputs.

        Returns:
            List of dicts: {date, type, total}
        """
        params = {"order": "Date DESC"}
        headers = {"If-Modified-Since": since_date.isoformat() + "T00:00:00"} if since_date else {}

        data = self._request("GET", "BankTransactions", params=params, headers=headers)
        rows = data.get("BankTransactions", [])
        if not isinstance(rows, list):
            logger.warning("Unexpected bank transactions response type: %s", type(rows))
            return []

        normalized = []
        for raw in rows:
            tx_date = _parse_xero_date(raw.get("DateString") or raw.get("Date"))
            if not tx_date:
                continue

            tx_type = str(raw.get("Type") or "").upper()
            try:
                total = float(raw.get("Total") or 0)
            except (TypeError, ValueError):
                total = 0.0

            normalized.append(
                {
                    "date": tx_date,
                    "type": tx_type,
                    "total": total,
                }
            )

        logger.info("Fetched %d bank transactions from Xero", len(normalized))
        return normalized

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
                "GET",
                "Invoices",
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
                    raw.get("InvoiceNumber", "?"),
                    e,
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
            line_items.append(
                {
                    "description": description,
                    "unit_amount": li.get("UnitAmount", 0),
                    "quantity": li.get("Quantity", 1),
                    "line_amount": li.get("LineAmount", 0),
                    "account_code": li.get("AccountCode"),
                }
            )

        bill_date = _parse_xero_date(raw.get("DateString") or raw.get("Date"))

        supplier_name = ""
        contact = raw.get("Contact")
        if contact and isinstance(contact, dict):
            supplier_name = contact.get("Name", "")

        return {
            "invoice_id": raw.get("InvoiceID", ""),
            "invoice_number": raw.get("InvoiceNumber", ""),
            "supplier": supplier_name,
            "date": bill_date,
            "status": raw.get("Status", ""),
            "total": raw.get("Total", 0),
            "line_items": line_items,
        }

    def fetch_profit_and_loss(self, from_date: date, to_date: date) -> dict:
        """
        Fetch Xero Profit & Loss report for a date range.

        Returns:
            {
                "total_income_cents": int,   # Total Income from the report
                "from_date": str,
                "to_date": str,
                "raw_sections": list,        # For debugging / detailed breakdown
            }
        """
        data = self._request(
            "GET",
            "Reports/ProfitAndLoss",
            params={
                "fromDate": from_date.isoformat(),
                "toDate": to_date.isoformat(),
            },
        )

        reports = data.get("Reports", [])
        if not reports:
            logger.warning("Xero P&L returned no reports for %s to %s", from_date, to_date)
            return {
                "total_income_cents": 0,
                "from_date": from_date.isoformat(),
                "to_date": to_date.isoformat(),
                "raw_sections": [],
            }

        report = reports[0]
        rows = report.get("Rows", [])

        total_income = 0.0
        raw_sections = []

        for section in rows:
            title = section.get("Title", "")
            row_type = section.get("RowType", "")

            if row_type == "Section" and "income" in title.lower():
                section_rows = section.get("Rows", [])
                for row in section_rows:
                    if row.get("RowType") == "SummaryRow":
                        cells = row.get("Cells", [])
                        if len(cells) >= 2:
                            try:
                                total_income = float(cells[1].get("Value", 0))
                            except (ValueError, TypeError):
                                pass
                raw_sections.append({"title": title, "rows": section_rows})

        logger.info(
            "Xero P&L %s to %s: Total Income = $%.2f",
            from_date,
            to_date,
            total_income,
        )

        return {
            "total_income_cents": round(total_income * 100),
            "from_date": from_date.isoformat(),
            "to_date": to_date.isoformat(),
            "raw_sections": raw_sections,
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
) -> dict:
    """
    Resolve line-item descriptions against approved mappings.
    LLM may propose new mappings but they remain non-authoritative until approved.

    Args:
        site_id: Site UUID
        line_items: List of dicts with 'description', 'unit_amount', 'quantity'

    Returns:
        {
            "mapped_lines": [...],  # lines allowed for downstream cost processing
            "approved_used": int,
            "proposals_created": int,
            "proposals_auto_applied": int,
            "review_additions": {reason: count}
        }
    """
    unique_descriptions = sorted({str(li.get("description") or "").strip() for li in line_items})
    unique_descriptions = [d for d in unique_descriptions if d]
    review_additions: dict[str, int] = defaultdict(int)

    approved: dict[str, dict] = {}
    existing_proposals: dict[str, dict] = {}
    uncached = []

    for desc in unique_descriptions:
        approved_mapping = get_xero_line_mapping(site_id, desc, status="approved")
        if approved_mapping:
            approved[desc] = approved_mapping
            continue
        proposed_mapping = get_xero_line_mapping(site_id, desc, status="proposed")
        if proposed_mapping:
            existing_proposals[desc] = proposed_mapping
            continue
        rejected_mapping = get_xero_line_mapping(site_id, desc, status="rejected")
        if rejected_mapping:
            existing_proposals[desc] = rejected_mapping
            continue
        uncached.append(desc)

    logger.info(
        "Xero line mapping workflow: %d approved, %d proposed, %d uncached",
        len(approved),
        len(existing_proposals),
        len(uncached),
    )

    llm_proposed: dict[str, dict] = {}
    proposals_created = 0
    if uncached and settings.ANTHROPIC_API_KEY:
        batch_size = 25
        for i in range(0, len(uncached), batch_size):
            batch = uncached[i : i + batch_size]
            batch_result = _llm_map_descriptions(site_id, batch)
            llm_proposed.update(batch_result)

        for desc, suggestion in llm_proposed.items():
            mapping_row = upsert_xero_line_mapping(
                site_id=site_id,
                description=desc,
                score_key=suggestion["score_key"],
                confidence=suggestion.get("confidence"),
                units_per_pack=suggestion.get("units_per_pack", 1),
                source="llm",
                status="proposed",
                model=suggestion.get("model"),
                prompt_version=suggestion.get("prompt_version"),
            )
            if mapping_row:
                existing_proposals[desc] = mapping_row
                proposals_created += 1

    allow_auto_apply = bool(settings.ALLOW_AUTO_APPLY_PROPOSED_MAPPINGS)
    min_auto_conf = float(settings.MIN_CONFIDENCE_AUTO_APPLY or 0.90)

    mapped_lines = []
    approved_used = 0
    proposals_auto_applied = 0

    for li in line_items:
        desc = str(li.get("description") or "").strip()
        if not desc:
            continue

        mapping = approved.get(desc)
        mapping_status = "approved"
        mapping_source = "approved"
        apply_allowed = mapping is not None

        if not mapping:
            mapping = existing_proposals.get(desc)
            mapping_status = str((mapping or {}).get("status") or "proposed")
            mapping_source = str((mapping or {}).get("source") or "llm")
            confidence = float((mapping or {}).get("confidence") or 0.0)
            apply_allowed = (
                mapping is not None
                and mapping_status == "proposed"
                and allow_auto_apply
                and confidence >= min_auto_conf
            )

            reason = None
            if not mapping:
                reason = "UNMAPPED"
            elif mapping_status == "rejected":
                reason = "UNMAPPED"
            elif confidence < min_auto_conf:
                reason = "LOW_CONFIDENCE"
            elif not allow_auto_apply:
                reason = "PENDING_APPROVAL"
            if reason:
                enqueue_xero_review_item(
                    site_id=site_id,
                    reason_code=reason,
                    invoice_id=li.get("_invoice_id"),
                    invoice_number=li.get("_invoice_number"),
                    supplier=li.get("_supplier"),
                    line_description=desc,
                    line_quantity=float(li.get("quantity", 1) or 1),
                    unit_amount=float(li.get("unit_amount", 0) or 0),
                    line_total=float(li.get("line_amount", 0) or 0),
                    bill_date=li.get("_bill_date"),
                    suggested_score_key=(mapping or {}).get("score_key"),
                    suggested_confidence=(mapping or {}).get("confidence"),
                    mapping_id=(mapping or {}).get("id"),
                    payload={
                        "mapping_status": mapping_status,
                        "mapping_source": mapping_source,
                        "auto_apply_enabled": allow_auto_apply,
                        "min_confidence_auto_apply": min_auto_conf,
                    },
                )
                review_additions[reason] += 1

        if not mapping or not apply_allowed:
            continue

        score_key = str(mapping.get("score_key") or "").strip()
        if not score_key:
            continue

        units_per_pack = max(1, int(mapping.get("units_per_pack") or 1))
        confidence = float(mapping.get("confidence") or 0.0)
        category = str((mapping or {}).get("category") or "ingredient")
        if mapping_status == "approved":
            approved_used += 1
        else:
            proposals_auto_applied += 1

        raw_cost_cents = int(round(float(li.get("unit_amount", 0)) * 100))
        unit_cost_cents = raw_cost_cents // max(1, units_per_pack)

        mapped_lines.append(
            {
                "description": desc,
                "score_key": score_key,
                "category": category,
                "unit_cost_cents": unit_cost_cents,
                "confidence": confidence,
                "units_per_pack": units_per_pack,
                "line_quantity": float(li.get("quantity", 1) or 1),
                "invoice_number": li.get("_invoice_number"),
                "invoice_id": li.get("_invoice_id"),
                "supplier": li.get("_supplier"),
                "bill_date": li.get("_bill_date"),
                "line_index": int(li.get("_line_index", 0) or 0),
                "line_total": float(li.get("line_amount", 0) or 0),
                "unit_amount": float(li.get("unit_amount", 0) or 0),
                "mapping_id": mapping.get("id"),
                "mapping_status": mapping_status,
            }
        )

    return {
        "mapped_lines": mapped_lines,
        "approved_used": approved_used,
        "proposals_created": proposals_created,
        "proposals_auto_applied": proposals_auto_applied,
        "review_additions": dict(review_additions),
    }


def _llm_map_descriptions(site_id: str, descriptions: list[str]) -> dict:
    """
    Use Claude to map Xero line descriptions to score_keys.

    Returns: {description: {score_key, category, confidence(float), units_per_pack, model, prompt_version}}
    """
    # Get existing score_keys for context
    existing_costs = get_item_costs(site_id)
    existing_keys = list(existing_costs.keys())
    try:
        inventory_items = list_inventory_items(site_id, active_only=True)
        inventory_keys = [i.get("score_key") for i in inventory_items if i.get("score_key")]
        existing_keys = sorted(list(set(existing_keys + inventory_keys)))
    except Exception:
        pass

    prompt = f"""You are mapping supplier invoice line items to menu item score_keys for a cafe.

Existing score_keys in the system:
{json.dumps(existing_keys, indent=2)}

Map each supplier line description to the most appropriate score_key.
- If it clearly matches an existing key, use that key
- If it's a new ingredient, create a snake_case key (e.g. "oat_milk", "vanilla_syrup")
- Category should be one of: "drink", "food", "retail", "ingredient"
- Confidence: numeric value from 0.00 to 1.00
- units_per_pack: how many individual sellable units are in this line item.
  Examples: "12 Pack- Milk Choc Chip" → 12, "12x 1L Oat Barista" → 12,
  "6x 1L Soy Milk" → 6, "100 Peppermint Tea Bags" → 100,
  "24x 600ml Spring Water" → 24, "1kg Matcha" → 1, "Plain Croissant" → 1
- Skip items that are clearly not menu-related (e.g. "cleaning supplies", "rent")

Supplier line descriptions to map:
{json.dumps(descriptions, indent=2)}

Respond with ONLY valid JSON — an array of objects:
[{{"description": "...", "score_key": "...", "category": "...", "confidence": 0.93, "units_per_pack": 1}}]

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
                confidence = m.get("confidence", 0.0)
                try:
                    confidence = max(0.0, min(1.0, float(confidence)))
                except (TypeError, ValueError):
                    confidence = 0.0
                result[desc] = {
                    "score_key": m["score_key"],
                    "category": m.get("category", "ingredient"),
                    "confidence": confidence,
                    "units_per_pack": max(1, int(m.get("units_per_pack", 1))),
                    "model": "claude-sonnet-4-5-20250929",
                    "prompt_version": settings.XERO_MAPPING_PROMPT_VERSION,
                }

        logger.info("LLM mapped %d/%d Xero descriptions", len(result), len(descriptions))
        return result

    except Exception as e:
        logger.warning("LLM mapping failed (non-fatal): %s", e)
        return {}


def _percentile(values: list[int], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(float(v) for v in values)
    idx = max(0.0, min(1.0, p)) * (len(ordered) - 1)
    lower = int(idx)
    upper = min(len(ordered) - 1, lower + 1)
    if lower == upper:
        return ordered[lower]
    frac = idx - lower
    return ordered[lower] * (1 - frac) + ordered[upper] * frac


def _iqr_bounds(values: list[int]) -> tuple[float, float]:
    q1 = _percentile(values, 0.25)
    q3 = _percentile(values, 0.75)
    iqr = max(0.0, q3 - q1)
    if iqr == 0:
        return q1 * 0.6, q3 * 1.4 if q3 else q1 * 1.4
    return q1 - (1.5 * iqr), q3 + (1.5 * iqr)


def _evaluate_cost_guardrails(
    *,
    site_id: str,
    score_key: str,
    proposed_cost_cents: int,
    current_cost_cents: Optional[int],
) -> tuple[bool, Optional[str], dict]:
    """
    Validate a proposed cost against configured delta clamp and historical outlier bounds.
    Returns (allowed, reason_code, context).
    """
    context: dict = {
        "score_key": score_key,
        "proposed_cost_cents": int(proposed_cost_cents),
    }

    if current_cost_cents is not None and int(current_cost_cents) > 0:
        delta_pct = (
            abs((int(proposed_cost_cents) - int(current_cost_cents)) / int(current_cost_cents))
            * 100.0
        )
        context["current_cost_cents"] = int(current_cost_cents)
        context["delta_pct"] = round(delta_pct, 2)
        if delta_pct > float(settings.MAX_COST_DELTA_PCT or 40.0):
            context["max_delta_pct"] = float(settings.MAX_COST_DELTA_PCT or 40.0)
            return False, "EXCESSIVE_DELTA", context

    history = get_recent_xero_cost_history(site_id, score_key, limit=30)
    context["history_points"] = len(history)
    if len(history) >= 4:
        lower, upper = _iqr_bounds(history)
        context["iqr_lower"] = round(lower, 2)
        context["iqr_upper"] = round(upper, 2)
        if proposed_cost_cents < lower or proposed_cost_cents > upper:
            return False, "OUTLIER_COST", context

    return True, None, context


# ============================================================
# Sync Pipeline
# ============================================================


def _aggregate_daily_cashflow(transactions: list[dict]) -> dict[date, dict]:
    """
    Aggregate normalized bank transactions into daily cashflow totals.
    """
    daily = defaultdict(lambda: {"income_cents": 0, "expense_cents": 0, "txn_count": 0})

    for tx in transactions:
        tx_date = tx.get("date")
        if not tx_date:
            continue

        tx_type = str(tx.get("type") or "").upper()
        total = float(tx.get("total") or 0.0)
        cents = int(round(abs(total) * 100))

        if tx_type.startswith("RECEIVE"):
            daily[tx_date]["income_cents"] += cents
        elif tx_type.startswith("SPEND"):
            daily[tx_date]["expense_cents"] += cents
        elif total < 0:
            daily[tx_date]["expense_cents"] += cents
        else:
            daily[tx_date]["income_cents"] += cents

        daily[tx_date]["txn_count"] += 1

    return dict(daily)


def sync_xero_bills(site_id: str, days_back: int = 30) -> dict:
    """
    Full sync: fetch bills -> map lines -> update item_costs.
    Called by daily scheduler and manual trigger.

    Returns:
        Summary dict: {bills_fetched, items_mapped, costs_updated}
    """
    since_date = date.today() - timedelta(days=days_back)

    review_additions: dict[str, int] = defaultdict(int)

    # 1. Fetch bills
    try:
        client = XeroClient(site_id)
        bills = client.fetch_bills(since_date=since_date)
    except XeroAuthError as e:
        enqueue_xero_review_item(
            site_id=site_id,
            reason_code="TOKEN_ERROR",
            line_description="Xero auth/token refresh failure",
            payload={"error": str(e)},
        )
        raise

    # 2. Collect and map line items for COGS updates.
    all_lines = []
    for bill in bills:
        for idx, line in enumerate(bill.get("line_items", [])):
            enriched = dict(line)
            enriched["_invoice_id"] = bill.get("invoice_id")
            enriched["_invoice_number"] = bill.get("invoice_number")
            enriched["_supplier"] = bill.get("supplier")
            enriched["_bill_date"] = bill.get("date")
            enriched["_line_index"] = idx
            all_lines.append(enriched)

    mapping_result = (
        map_xero_lines_to_score_keys(site_id, all_lines)
        if all_lines
        else {
            "mapped_lines": [],
            "approved_used": 0,
            "proposals_created": 0,
            "proposals_auto_applied": 0,
            "review_additions": {},
        }
    )
    if isinstance(mapping_result, list):
        mapped = mapping_result
        approved_used = 0
        proposals_created = 0
        proposals_auto_applied = 0
    else:
        mapped = mapping_result.get("mapped_lines", [])
        approved_used = int(mapping_result.get("approved_used") or 0)
        proposals_created = int(mapping_result.get("proposals_created") or 0)
        proposals_auto_applied = int(mapping_result.get("proposals_auto_applied") or 0)
        for reason, count in (mapping_result.get("review_additions") or {}).items():
            review_additions[str(reason)] += int(count or 0)

    costs_updated = 0
    receipts_linked = 0
    receipts_unmatched = 0
    current_costs = get_item_costs(site_id)
    for item in mapped:
        score_key = item["score_key"]
        proposed_cost = int(item["unit_cost_cents"])
        allowed, reason_code, guard_context = _evaluate_cost_guardrails(
            site_id=site_id,
            score_key=score_key,
            proposed_cost_cents=proposed_cost,
            current_cost_cents=current_costs.get(score_key),
        )
        if not allowed:
            enqueue_xero_review_item(
                site_id=site_id,
                reason_code=reason_code or "OUTLIER_COST",
                invoice_id=item.get("invoice_id"),
                invoice_number=item.get("invoice_number"),
                supplier=item.get("supplier"),
                line_description=item.get("description") or score_key,
                line_quantity=float(item.get("line_quantity", 1) or 1),
                unit_amount=float(item.get("unit_amount", 0) or 0),
                line_total=float(item.get("line_total", 0) or 0),
                bill_date=item.get("bill_date"),
                suggested_score_key=score_key,
                suggested_confidence=item.get("confidence"),
                mapping_id=item.get("mapping_id"),
                payload=guard_context,
            )
            review_additions[reason_code or "OUTLIER_COST"] += 1
            continue

        upsert_item_cost(
            site_id=site_id,
            score_key=score_key,
            category=item["category"],
            cost_cents=proposed_cost,
            description=item["description"],
            source="xero",
        )
        store_xero_cost_history(
            site_id=site_id,
            score_key=score_key,
            cost_cents=proposed_cost,
            source="xero",
            reference=item.get("invoice_number"),
        )
        current_costs[score_key] = proposed_cost
        costs_updated += 1

        # Optional stock receipt import: only if inventory item exists for this score_key.
        inventory_item = get_inventory_item_by_score_key(site_id, score_key)
        if not inventory_item:
            receipts_unmatched += 1
            continue

        line_qty = float(item.get("line_quantity") or 0)
        units_per_pack = max(1, int(item.get("units_per_pack") or 1))
        received_units = line_qty * units_per_pack
        if received_units <= 0:
            continue

        bill_date = item.get("bill_date")
        received_at = None
        if isinstance(bill_date, date):
            received_at = datetime.combine(bill_date, datetime.min.time())

        invoice_number = item.get("invoice_number") or "unknown"
        line_index = int(item.get("line_index") or 0)
        external_ref = f"XERO:{invoice_number}:{line_index}:{inventory_item['inventory_item_id']}"

        receipt_id = store_inventory_receipt(
            site_id=site_id,
            inventory_item_id=str(inventory_item["inventory_item_id"]),
            quantity_units=received_units,
            received_at=received_at,
            unit_cost_cents=item.get("unit_cost_cents"),
            supplier_name=item.get("supplier"),
            source="xero",
            external_ref=external_ref,
            raw_line_description=item.get("description"),
        )
        if receipt_id:
            receipts_linked += 1

        if item.get("mapping_status") == "proposed" and item.get("mapping_id"):
            resolve_xero_review_items_for_mapping(
                site_id=site_id,
                mapping_id=int(item["mapping_id"]),
                resolved_by="system:auto_apply",
                resolution_note="Auto-applied during Xero sync after guardrails passed.",
            )

    if not bills:
        logger.info("No Xero bills found since %s", since_date)
    elif not all_lines:
        logger.info("No line items in %d bills", len(bills))

    # 3. Fetch bank transactions and persist factual daily in/out.
    financial_days_updated = 0
    financial_txns = 0
    try:
        if hasattr(client, "fetch_bank_transactions"):
            txns = client.fetch_bank_transactions(since_date=since_date)
            daily_cashflow = _aggregate_daily_cashflow(txns)
            for tx_date, totals in daily_cashflow.items():
                upsert_xero_financial_fact(
                    site_id=site_id,
                    fact_date=tx_date,
                    income_cents=totals["income_cents"],
                    expense_cents=totals["expense_cents"],
                    payroll_cents=None,
                    txn_count=totals["txn_count"],
                    source="xero_bank_transactions",
                    completeness="partial",
                )
                financial_days_updated += 1
            financial_txns = len(txns)
    except XeroAuthError as e:
        enqueue_xero_review_item(
            site_id=site_id,
            reason_code="TOKEN_ERROR",
            line_description="Xero auth/token refresh failure during bank transaction sync",
            payload={"error": str(e)},
        )
        review_additions["TOKEN_ERROR"] += 1
        logger.warning("Xero bank transaction sync auth failed (non-fatal): %s", e)
    except XeroError as e:
        logger.warning("Xero bank transaction sync failed (non-fatal): %s", e)
    except Exception as e:
        logger.warning("Xero financial fact sync unexpected error (non-fatal): %s", e)

    # 4. Revenue reconciliation: use Xero period totals to backfill missing Square days.
    revenue_reconciliation = {
        "weeks_processed": 0,
        "weeks_reconciled": 0,
        "days_reconciled": 0,
    }
    try:
        revenue_reconciliation = sync_xero_revenue(
            site_id=site_id,
            weeks_back=8,
            settlement_lag_days=2,
            client=client,
        )
    except XeroAuthError as e:
        enqueue_xero_review_item(
            site_id=site_id,
            reason_code="TOKEN_ERROR",
            line_description="Xero auth/token refresh failure during revenue reconciliation",
            payload={"error": str(e)},
        )
        review_additions["TOKEN_ERROR"] += 1
        logger.warning("Xero revenue reconciliation auth failed (non-fatal): %s", e)
    except XeroError as e:
        logger.warning("Xero revenue reconciliation failed (non-fatal): %s", e)
    except Exception as e:
        logger.warning("Xero revenue reconciliation unexpected error (non-fatal): %s", e)

    logger.info(
        "Xero sync complete: bills=%d lines=%d mapped=%d updated=%d approved=%d proposed=%d auto_applied=%d review=%d",
        len(bills),
        len(all_lines),
        len(mapped),
        costs_updated,
        approved_used,
        proposals_created,
        proposals_auto_applied,
        int(sum(review_additions.values())),
    )

    return {
        "bills_fetched": len(bills),
        "lines_processed": len(all_lines),
        "items_mapped": len(mapped),
        "mappings_approved_used": approved_used,
        "mappings_proposed": proposals_created,
        "mappings_auto_applied": proposals_auto_applied,
        "costs_updated": costs_updated,
        "items_updated": costs_updated,
        "inventory_receipts_linked": receipts_linked,
        "inventory_receipts_unmatched": receipts_unmatched,
        "review_queue_added": int(sum(review_additions.values())),
        "review_queue_by_reason": dict(sorted(review_additions.items())),
        "financial_transactions": financial_txns,
        "financial_days_updated": financial_days_updated,
        "revenue_weeks_processed": int(revenue_reconciliation.get("weeks_processed") or 0),
        "revenue_weeks_reconciled": int(revenue_reconciliation.get("weeks_reconciled") or 0),
        "revenue_days_reconciled": int(revenue_reconciliation.get("days_reconciled") or 0),
    }


# ============================================================
# Xero Revenue Cross-Check
# ============================================================


def _iter_days(start_date: date, end_date: date):
    current = start_date
    while current <= end_date:
        yield current
        current += timedelta(days=1)


def _last_sunday(on_or_before: date) -> date:
    # Python weekday: Mon=0..Sun=6
    days_since_sunday = (on_or_before.weekday() + 1) % 7
    return on_or_before - timedelta(days=days_since_sunday)


def _python_to_sql_dow(d: date) -> int:
    # SQL EXTRACT(DOW): Sun=0, Mon=1, ... Sat=6
    return (d.weekday() + 1) % 7


def _build_square_revenue_by_day(site_id: str, start_date: date, end_date: date) -> dict[str, int]:
    """
    Build Square daily revenue map using orders_raw baseline, overridden by
    daily_sales_history gross values when available.
    """
    from data.storage import _text, engine

    with engine.connect() as conn:
        orders_rows = (
            conn.execute(
                _text(
                    """
                SELECT DATE(closed_at) AS day, COALESCE(SUM(total_money_cents), 0) AS revenue_cents
                FROM orders_raw
                WHERE site_id = :sid
                  AND closed_at IS NOT NULL
                  AND DATE(closed_at) >= :s
                  AND DATE(closed_at) <= :e
                GROUP BY DATE(closed_at)
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            .mappings()
            .all()
        )

        history_rows = (
            conn.execute(
                _text(
                    """
                SELECT sale_date AS day, gross_sales_cents, source
                FROM daily_sales_history
                WHERE site_id = :sid
                  AND sale_date >= :s
                  AND sale_date <= :e
                """
                ),
                {"sid": site_id, "s": start_date, "e": end_date},
            )
            .mappings()
            .all()
        )

    revenue_by_day: dict[str, int] = {}
    for row in orders_rows:
        revenue_by_day[str(row["day"])] = int(row["revenue_cents"] or 0)

    for row in history_rows:
        day_str = str(row["day"])
        gross = int(row["gross_sales_cents"] or 0)
        source = str(row.get("source") or "").lower()
        if gross > 0 and source in ("csv", "api", "square", "square_csv"):
            revenue_by_day[day_str] = gross

    return revenue_by_day


def _historical_dow_averages(
    site_id: str, lookback_start: date, lookback_end: date
) -> dict[int, float]:
    """
    Return average Square revenue by SQL DOW across a historical lookback window.
    """
    from data.storage import _text, engine

    with engine.connect() as conn:
        rows = (
            conn.execute(
                _text(
                    """
                WITH daily AS (
                    SELECT DATE(closed_at) AS day, COALESCE(SUM(total_money_cents), 0) AS revenue_cents
                    FROM orders_raw
                    WHERE site_id = :sid
                      AND closed_at IS NOT NULL
                      AND DATE(closed_at) >= :s
                      AND DATE(closed_at) <= :e
                    GROUP BY DATE(closed_at)
                )
                SELECT EXTRACT(DOW FROM day)::int AS dow, AVG(revenue_cents)::numeric AS avg_revenue_cents
                FROM daily
                GROUP BY EXTRACT(DOW FROM day)
                """
                ),
                {"sid": site_id, "s": lookback_start, "e": lookback_end},
            )
            .mappings()
            .all()
        )

    return {int(row["dow"]): float(row["avg_revenue_cents"] or 0.0) for row in rows}


def _allocate_delta_to_missing_days(
    missing_days: list[date],
    delta_cents: int,
    dow_averages_cents: dict[int, float],
) -> dict[date, int]:
    """
    Allocate period delta across missing days using DOW revenue weights.
    """
    if not missing_days or delta_cents <= 0:
        return {}

    if len(missing_days) == 1:
        return {missing_days[0]: int(delta_cents)}

    weights: dict[date, float] = {}
    for day in missing_days:
        dow = _python_to_sql_dow(day)
        weight = float(dow_averages_cents.get(dow) or 0.0)
        weights[day] = weight if weight > 0 else 1.0

    total_weight = sum(weights.values())
    if total_weight <= 0:
        per_day = delta_cents // len(missing_days)
        allocations = {d: per_day for d in missing_days}
        allocations[missing_days[0]] += delta_cents - (per_day * len(missing_days))
        return allocations

    raw = {d: (delta_cents * weights[d] / total_weight) for d in missing_days}
    allocations = {d: int(round(v)) for d, v in raw.items()}
    drift = int(delta_cents - sum(allocations.values()))
    if drift != 0:
        # Push rounding drift to the heaviest-weight day.
        target = max(missing_days, key=lambda d: weights[d])
        allocations[target] += drift

    return {d: max(0, int(v)) for d, v in allocations.items()}


def sync_xero_revenue(
    site_id: str,
    weeks_back: int = 8,
    settlement_lag_days: int = 2,
    client: Optional[XeroClient] = None,
) -> dict:
    """
    Reconcile settled weekly Xero revenue totals against Square daily revenue.

    For each settled week (end date <= today - settlement_lag_days):
      - Fetch absolute Xero revenue total for the week.
      - Sum Square revenue for known days.
      - If Square has missing days, allocate Xero delta to missing day(s).
      - Persist allocations into daily_sales_history.xero_revenue_cents.
    """
    from data.storage import store_xero_daily_revenue

    if weeks_back <= 0:
        return {"weeks_processed": 0, "weeks_reconciled": 0, "days_reconciled": 0, "details": []}

    client = client or XeroClient(site_id)

    settled_end = date.today() - timedelta(days=max(0, settlement_lag_days))
    last_settled_sunday = _last_sunday(settled_end)

    weeks_processed = 0
    weeks_reconciled = 0
    days_reconciled = 0
    details = []

    for i in range(weeks_back):
        week_end = last_settled_sunday - timedelta(days=7 * i)
        week_start = week_end - timedelta(days=6)
        if week_end >= date.today():
            continue

        weeks_processed += 1

        pnl = client.fetch_profit_and_loss(week_start, week_end)
        xero_total = int(pnl.get("total_income_cents") or 0)  # already ex-GST

        # Square is inc-GST — strip GST so comparison is ex-GST on both sides.
        # All business decisions use ex-GST (true cash position, GST is ATO pass-through).
        from config.constants import GST_RATE

        gst_divisor = 1 + GST_RATE  # 1.10

        square_by_day = _build_square_revenue_by_day(site_id, week_start, week_end)
        week_days = list(_iter_days(week_start, week_end))
        missing_days = [d for d in week_days if int(square_by_day.get(str(d)) or 0) <= 0]
        # Convert Square known totals to ex-GST for apples-to-apples comparison
        known_total_inc_gst = sum(
            int(square_by_day.get(str(d)) or 0) for d in week_days if d not in missing_days
        )
        known_total = round(known_total_inc_gst / gst_divisor)
        delta = xero_total - known_total  # both ex-GST

        week_detail = {
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "xero_income_cents": xero_total,
            "square_known_ex_gst_cents": known_total,
            "square_known_inc_gst_cents": known_total_inc_gst,
            "missing_days": [d.isoformat() for d in missing_days],
            "delta_cents": delta,
            "status": "no_action",
            "days_reconciled": 0,
        }

        if xero_total <= 0:
            week_detail["status"] = "no_xero_income"
            details.append(week_detail)
            continue

        if not missing_days:
            week_detail["status"] = "no_missing_days"
            details.append(week_detail)
            continue

        if delta <= 0:
            week_detail["status"] = "delta_non_positive"
            details.append(week_detail)
            continue

        lookback_end = week_start - timedelta(days=1)
        lookback_start = lookback_end - timedelta(days=56)
        dow_avgs = _historical_dow_averages(site_id, lookback_start, lookback_end)
        allocations = _allocate_delta_to_missing_days(missing_days, delta, dow_avgs)

        reconciled_this_week = 0
        for day, amount_cents in allocations.items():
            if amount_cents <= 0:
                continue
            store_xero_daily_revenue(site_id, day, amount_cents)
            reconciled_this_week += 1

        if reconciled_this_week > 0:
            weeks_reconciled += 1
            days_reconciled += reconciled_this_week
            week_detail["status"] = (
                "single_day_delta" if len(missing_days) == 1 else "multi_day_weighted_delta"
            )
            week_detail["days_reconciled"] = reconciled_this_week
            week_detail["allocations"] = {
                d.isoformat(): int(cents) for d, cents in allocations.items()
            }
        else:
            week_detail["status"] = "no_positive_allocation"

        details.append(week_detail)

    return {
        "weeks_processed": weeks_processed,
        "weeks_reconciled": weeks_reconciled,
        "days_reconciled": days_reconciled,
        "settlement_lag_days": settlement_lag_days,
        "details": details,
    }
