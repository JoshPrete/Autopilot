from data.xero import sync_xero_bills


class _DummyClient:
    def __init__(self, bills):
        self._bills = bills

    def fetch_bills(self, since_date=None):
        return self._bills


class TestSyncXeroBills:
    def test_returns_zero_summary_when_no_bills(self, monkeypatch):
        monkeypatch.setattr("data.xero.XeroClient", lambda _sid: _DummyClient([]))

        result = sync_xero_bills("site-1")

        assert result == {"bills_fetched": 0, "items_mapped": 0, "costs_updated": 0}

    def test_maps_and_upserts_line_items(self, monkeypatch):
        bills = [
            {
                "invoice_number": "INV-1",
                "line_items": [
                    {"description": "Milk 2L", "unit_amount": 6.5, "quantity": 1},
                    {"description": "Coffee Beans 1kg", "unit_amount": 42.0, "quantity": 1},
                ],
            }
        ]
        mapped = [
            {
                "description": "Milk 2L",
                "score_key": "latte",
                "category": "drink",
                "unit_cost_cents": 650,
                "confidence": "high",
            },
            {
                "description": "Coffee Beans 1kg",
                "score_key": "beans",
                "category": "retail",
                "unit_cost_cents": 4200,
                "confidence": "high",
            },
        ]
        upserts = []

        monkeypatch.setattr("data.xero.XeroClient", lambda _sid: _DummyClient(bills))
        monkeypatch.setattr("data.xero.map_xero_lines_to_score_keys", lambda _sid, _lines: mapped)
        monkeypatch.setattr(
            "data.xero.upsert_item_cost",
            lambda **kwargs: upserts.append(kwargs),
        )

        result = sync_xero_bills("site-1")

        assert result["bills_fetched"] == 1
        assert result["items_mapped"] == 2
        assert result["costs_updated"] == 2
        assert len(upserts) == 2
        assert upserts[0]["source"] == "xero"
