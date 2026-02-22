"""
Tests for data/ingestion.py - Square Order Parsing

Covers:
- Raw order parsing from Square API format
- Line item extraction
- Modifier extraction
"""

import pytest

from data.ingestion import parse_order, parse_orders


# ============================================================
# Parse Single Order
# ============================================================


class TestParseOrder:
    def test_basic_order(self):
        raw = {
            "id": "order-123",
            "created_at": "2026-02-07T08:45:00Z",
            "closed_at": "2026-02-07T08:48:00Z",
            "total_money": {"amount": 1650, "currency": "AUD"},
            "state": "COMPLETED",
            "line_items": [
                {
                    "name": "Latte",
                    "quantity": "2",
                    "catalog_object_id": "cat-001",
                    "modifiers": [{"name": "Oat Milk", "catalog_object_id": "mod-001"}],
                }
            ],
        }

        result = parse_order(raw)

        assert result["order_id"] == "order-123"
        assert result["state"] == "COMPLETED"
        assert result["total_money_cents"] == 1650
        assert len(result["line_items"]) == 1
        assert result["line_items"][0]["item_name"] == "Latte"
        assert result["line_items"][0]["quantity"] == 2
        assert len(result["line_items"][0]["modifiers"]) == 1

    def test_no_line_items(self):
        raw = {
            "id": "order-empty",
            "created_at": "2026-02-07T08:00:00Z",
            "closed_at": "2026-02-07T08:01:00Z",
            "state": "COMPLETED",
        }

        result = parse_order(raw)
        assert result["line_items"] == []

    def test_no_modifiers(self):
        raw = {
            "id": "order-nomod",
            "created_at": "2026-02-07T08:00:00Z",
            "closed_at": "2026-02-07T08:01:00Z",
            "state": "COMPLETED",
            "line_items": [
                {"name": "Espresso", "quantity": "1"},
            ],
        }

        result = parse_order(raw)
        assert result["line_items"][0]["modifiers"] == []

    def test_missing_money(self):
        raw = {
            "id": "order-nomoney",
            "created_at": "2026-02-07T08:00:00Z",
            "state": "COMPLETED",
        }

        result = parse_order(raw)
        assert result["total_money_cents"] == 0


class TestParseOrders:
    def test_batch_parsing(self):
        raw_orders = [
            {
                "id": f"order-{i}",
                "created_at": "2026-02-07T08:00:00Z",
                "closed_at": "2026-02-07T08:01:00Z",
                "state": "COMPLETED",
                "line_items": [{"name": "Latte", "quantity": "1"}],
            }
            for i in range(5)
        ]

        results = parse_orders(raw_orders)
        assert len(results) == 5
        assert all(r["order_id"].startswith("order-") for r in results)

    def test_empty_batch(self):
        assert parse_orders([]) == []
