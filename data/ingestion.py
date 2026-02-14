"""
Clubhouse Autopilot v1.2 - Data Ingestion
Square Orders API integration (Spec Section 7.1, 7.2)

Pulls completed orders from Square, extracts line items and modifiers,
and stores raw order data for workload processing.
"""

import logging
from datetime import datetime, timedelta, timezone

from square.client import Client as SquareClient

from config.settings import settings

# Brisbane is always UTC+10 (Queensland has no DST)
AEST = timezone(timedelta(hours=10))

logger = logging.getLogger("autopilot.ingestion")


class SquareIngestionError(Exception):
    pass


class SquareIngestion:
    """Fetches orders and catalog data from Square POS API."""

    def __init__(self, access_token: str = None, location_id: str = None):
        self.access_token = access_token or settings.SQUARE_ACCESS_TOKEN
        self.location_id = location_id or settings.SQUARE_LOCATION_ID

        if not self.access_token:
            raise SquareIngestionError("SQUARE_ACCESS_TOKEN not configured")
        if not self.location_id:
            raise SquareIngestionError("SQUARE_LOCATION_ID not configured")

        self.client = SquareClient(
            access_token=self.access_token,
            environment=settings.SQUARE_ENVIRONMENT,
        )

    def fetch_completed_orders(
        self,
        start_time: datetime,
        end_time: datetime,
        batch_size: int = 500,
    ) -> list[dict]:
        """
        Fetch completed orders from Square for a time window.

        Uses closed_at filter per spec Section 7.1:
        POST /v2/orders/search with COMPLETED state filter.
        Paginates automatically via cursor.
        """
        all_orders = []
        cursor = None

        start_iso = start_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        end_iso = end_time.strftime("%Y-%m-%dT%H:%M:%S.000Z")

        logger.info(
            "Fetching orders: %s to %s (location: %s)",
            start_iso, end_iso, self.location_id,
        )

        while True:
            body = {
                "location_ids": [self.location_id],
                "query": {
                    "filter": {
                        "state_filter": {"states": ["COMPLETED"]},
                        "date_time_filter": {
                            "closed_at": {
                                "start_at": start_iso,
                                "end_at": end_iso,
                            }
                        },
                    },
                    "sort": {"sort_field": "CLOSED_AT", "sort_order": "ASC"},
                },
                "limit": batch_size,
            }
            if cursor:
                body["cursor"] = cursor

            result = self.client.orders.search_orders(body=body)

            if result.is_error():
                logger.error("Square API error: %s", result.errors)
                raise SquareIngestionError(f"Square API error: {result.errors}")

            response = result.body
            orders = response.get("orders", [])
            all_orders.extend(orders)

            cursor = response.get("cursor")
            if not cursor:
                break

            logger.debug("Fetched %d orders so far, paginating...", len(all_orders))

        logger.info("Fetched %d total orders", len(all_orders))
        return all_orders

    def fetch_todays_orders(self) -> list[dict]:
        """Fetch all completed orders for today (AEST day boundaries)."""
        now_aest = datetime.now(AEST)
        start_of_day_aest = now_aest.replace(hour=0, minute=0, second=0, microsecond=0)
        # Convert AEST boundaries to UTC for Square API
        start_utc = start_of_day_aest.astimezone(timezone.utc).replace(tzinfo=None)
        now_utc = now_aest.astimezone(timezone.utc).replace(tzinfo=None)
        return self.fetch_completed_orders(start_utc, now_utc)

    def fetch_date_range(self, date: datetime) -> list[dict]:
        """Fetch all completed orders for a full calendar day (AEST boundaries)."""
        # Treat the date as AEST, convert to UTC for Square API
        start_aest = date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=AEST)
        end_aest = start_aest + timedelta(days=1)
        start_utc = start_aest.astimezone(timezone.utc).replace(tzinfo=None)
        end_utc = end_aest.astimezone(timezone.utc).replace(tzinfo=None)
        return self.fetch_completed_orders(start_utc, end_utc)

    def fetch_catalog(self) -> dict:
        """
        Fetch catalog items and modifiers from Square (Spec Section 7.2).

        Returns dict with 'items' and 'modifiers' keys, each mapping
        catalog_id -> detail dict.
        """
        items = {}
        modifiers = {}
        cursor = None

        logger.info("Fetching Square catalog...")

        while True:
            kwargs = {"types": "ITEM,MODIFIER_LIST"}
            if cursor:
                kwargs["cursor"] = cursor

            result = self.client.catalog.list_catalog(**kwargs)

            if result.is_error():
                logger.error("Square catalog error: %s", result.errors)
                raise SquareIngestionError(f"Square catalog error: {result.errors}")

            body = result.body
            for obj in body.get("objects", []):
                obj_type = obj.get("type")
                obj_id = obj.get("id")

                if obj_type == "ITEM":
                    item_data = obj.get("item_data", {})
                    items[obj_id] = {
                        "catalog_item_id": obj_id,
                        "item_name": item_data.get("name", "Unknown"),
                        "category_id": item_data.get("category_id"),
                        "variations": [
                            {
                                "id": v.get("id"),
                                "name": v.get("item_variation_data", {}).get("name"),
                            }
                            for v in item_data.get("variations", [])
                        ],
                    }
                elif obj_type == "MODIFIER_LIST":
                    mod_data = obj.get("modifier_list_data", {})
                    for mod in mod_data.get("modifiers", []):
                        mod_id = mod.get("id")
                        mod_detail = mod.get("modifier_data", {})
                        modifiers[mod_id] = {
                            "catalog_modifier_id": mod_id,
                            "modifier_name": mod_detail.get("name", "Unknown"),
                            "list_id": obj_id,
                        }

            cursor = body.get("cursor")
            if not cursor:
                break

        logger.info("Fetched catalog: %d items, %d modifiers", len(items), len(modifiers))
        return {"items": items, "modifiers": modifiers}


def _utc_to_aest(iso_str: str) -> str:
    """Convert a UTC ISO timestamp string to AEST (UTC+10), return as ISO string."""
    if not iso_str:
        return iso_str
    try:
        # Square returns "2026-02-11T00:02:44.712Z" format
        cleaned = iso_str.replace("Z", "+00:00")
        dt_utc = datetime.fromisoformat(cleaned)
        dt_aest = dt_utc.astimezone(AEST)
        # Return naive datetime string (strip tzinfo) for DB storage
        return dt_aest.replace(tzinfo=None).isoformat()
    except (ValueError, TypeError):
        return iso_str


def parse_order(order: dict) -> dict:
    """
    Parse a raw Square order into normalized format.

    Extracts order_id, timestamps (converted to AEST), total, state,
    and line_items with their catalog IDs, modifiers, and quantities.
    """
    total_money = order.get("total_money", {})

    line_items = []
    for idx, li in enumerate(order.get("line_items", []), start=1):
        item_modifiers = []
        for mod in li.get("modifiers", []):
            item_modifiers.append({
                "catalog_modifier_id": mod.get("catalog_object_id"),
                "name": mod.get("name", ""),
                "amount_cents": mod.get("total_money", {}).get("amount", 0),
            })

        line_items.append({
            "catalog_item_id": li.get("catalog_object_id"),
            "item_name": li.get("name", "Unknown Item"),
            "quantity": int(li.get("quantity", "1")),
            "position_in_order": idx,
            "modifiers": item_modifiers,
        })

    return {
        "order_id": order.get("id"),
        "created_at": _utc_to_aest(order.get("created_at")),
        "closed_at": _utc_to_aest(order.get("closed_at")),
        "total_money_cents": total_money.get("amount", 0),
        "state": order.get("state"),
        "line_items": line_items,
        "payload": order,
    }


def parse_orders(raw_orders: list[dict]) -> list[dict]:
    """Parse a list of raw Square orders into normalized format."""
    parsed = []
    for order in raw_orders:
        try:
            parsed.append(parse_order(order))
        except Exception:
            logger.exception("Failed to parse order %s", order.get("id", "unknown"))
    return parsed
