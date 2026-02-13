from __future__ import annotations

import logging
import uuid
from typing import Optional

from square.client import Client as SquareClient

from config.settings import settings

logger = logging.getLogger("voice.square")


class SquareVoiceServiceError(RuntimeError):
    pass


class SquareVoiceService:
    def __init__(self, access_token: Optional[str] = None, location_id: Optional[str] = None):
        self.access_token = access_token or settings.SQUARE_ACCESS_TOKEN
        self.location_id = location_id or settings.SQUARE_LOCATION_ID

        if not self.access_token:
            raise SquareVoiceServiceError("SQUARE_ACCESS_TOKEN not configured")
        if not self.location_id:
            raise SquareVoiceServiceError("SQUARE_LOCATION_ID not configured")

        self.client = SquareClient(
            access_token=self.access_token,
            environment=settings.SQUARE_ENVIRONMENT,
        )

    def fetch_catalog(self) -> dict:
        from data.ingestion import SquareIngestion

        ingestion = SquareIngestion(self.access_token, self.location_id)
        return ingestion.fetch_catalog()

    def create_order(
        self,
        line_items: list,
        pickup_at: Optional[str] = None,
        recipient_name: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> dict:
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "order": {
                "location_id": location_id or self.location_id,
                "line_items": line_items,
            },
        }

        if pickup_at:
            payload["order"]["fulfillments"] = [
                {
                    "type": "PICKUP",
                    "state": "PROPOSED",
                    "pickup_details": {
                        "schedule_type": "SCHEDULED",
                        "pickup_at": pickup_at,
                        "recipient": {
                            "display_name": recipient_name or "Walk-in",
                        },
                    },
                }
            ]

        result = self.client.orders.create_order(body=payload)
        if result.is_error():
            logger.error("Square create_order error: %s", result.errors)
            raise SquareVoiceServiceError(result.errors)
        return result.body.get("order", {})

    def create_terminal_checkout(
        self,
        order_id: str,
        device_id: str,
        amount_money: Optional[dict] = None,
        note: Optional[str] = None,
    ) -> dict:
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "checkout": {
                "order_id": order_id,
                "device_options": {"device_id": device_id},
            },
        }
        if amount_money:
            payload["checkout"]["amount_money"] = amount_money
        if note:
            payload["checkout"]["note"] = note

        result = self.client.terminal.create_terminal_checkout(body=payload)
        if result.is_error():
            logger.error("Square create_terminal_checkout error: %s", result.errors)
            raise SquareVoiceServiceError(result.errors)
        return result.body.get("checkout", {})

    def create_payment(
        self,
        order_id: str,
        amount_money: dict,
        source_id: str,
        note: Optional[str] = None,
        location_id: Optional[str] = None,
    ) -> dict:
        payload = {
            "idempotency_key": str(uuid.uuid4()),
            "source_id": source_id,
            "amount_money": amount_money,
            "order_id": order_id,
            "location_id": location_id or self.location_id,
        }
        if note:
            payload["note"] = note

        result = self.client.payments.create_payment(body=payload)
        if result.is_error():
            logger.error("Square create_payment error: %s", result.errors)
            raise SquareVoiceServiceError(result.errors)
        return result.body.get("payment", {})
