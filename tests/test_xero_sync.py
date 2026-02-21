from datetime import date

from data.xero import _allocate_delta_to_missing_days, sync_xero_bills


class _DummyClient:
    def __init__(self, bills, txns=None):
        self._bills = bills
        self._txns = txns or []

    def fetch_bills(self, since_date=None):
        return self._bills

    def fetch_bank_transactions(self, since_date=None):
        return self._txns

    def fetch_profit_and_loss(self, from_date, to_date):
        return {"total_income_cents": 0, "from_date": str(from_date), "to_date": str(to_date)}


class TestSyncXeroBills:
    def test_returns_zero_summary_when_no_bills(self, monkeypatch):
        monkeypatch.setattr("data.xero.XeroClient", lambda _sid: _DummyClient([]))
        monkeypatch.setattr(
            "data.xero.sync_xero_revenue",
            lambda *_args, **_kwargs: {
                "weeks_processed": 0,
                "weeks_reconciled": 0,
                "days_reconciled": 0,
            },
        )

        result = sync_xero_bills("site-1")

        assert result == {
            "bills_fetched": 0,
            "items_mapped": 0,
            "costs_updated": 0,
            "inventory_receipts_linked": 0,
            "inventory_receipts_unmatched": 0,
            "financial_transactions": 0,
            "financial_days_updated": 0,
            "revenue_weeks_processed": 0,
            "revenue_weeks_reconciled": 0,
            "revenue_days_reconciled": 0,
        }

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
            "data.xero.sync_xero_revenue",
            lambda *_args, **_kwargs: {
                "weeks_processed": 2,
                "weeks_reconciled": 1,
                "days_reconciled": 1,
            },
        )
        monkeypatch.setattr(
            "data.xero.upsert_item_cost",
            lambda **kwargs: upserts.append(kwargs),
        )

        result = sync_xero_bills("site-1")

        assert result["bills_fetched"] == 1
        assert result["items_mapped"] == 2
        assert result["costs_updated"] == 2
        assert result["inventory_receipts_linked"] == 0
        assert len(upserts) == 2
        assert upserts[0]["source"] == "xero"
        assert result["financial_transactions"] == 0
        assert result["financial_days_updated"] == 0
        assert result["revenue_weeks_processed"] == 2
        assert result["revenue_weeks_reconciled"] == 1
        assert result["revenue_days_reconciled"] == 1

    def test_upserts_daily_financial_facts_from_bank_transactions(self, monkeypatch):
        txns = [
            {"date": "2026-02-18", "type": "RECEIVE", "total": 1250.75},
            {"date": "2026-02-18", "type": "SPEND", "total": 420.10},
            {"date": "2026-02-19", "type": "SPEND", "total": 100.00},
        ]
        facts = []

        monkeypatch.setattr("data.xero.XeroClient", lambda _sid: _DummyClient([], txns=txns))
        monkeypatch.setattr(
            "data.xero.sync_xero_revenue",
            lambda *_args, **_kwargs: {
                "weeks_processed": 0,
                "weeks_reconciled": 0,
                "days_reconciled": 0,
            },
        )
        monkeypatch.setattr(
            "data.xero.upsert_xero_financial_fact",
            lambda **kwargs: facts.append(kwargs),
        )

        result = sync_xero_bills("site-1")

        assert result["bills_fetched"] == 0
        assert result["financial_transactions"] == 3
        assert result["financial_days_updated"] == 2
        assert result["inventory_receipts_linked"] == 0
        assert len(facts) == 2
        by_date = {str(row["fact_date"]): row for row in facts}
        assert by_date["2026-02-18"]["income_cents"] == 125075
        assert by_date["2026-02-18"]["expense_cents"] == 42010
        assert by_date["2026-02-18"]["txn_count"] == 2
        assert by_date["2026-02-19"]["income_cents"] == 0
        assert by_date["2026-02-19"]["expense_cents"] == 10000
        assert result["revenue_weeks_processed"] == 0
        assert result["revenue_weeks_reconciled"] == 0
        assert result["revenue_days_reconciled"] == 0

    def test_links_inventory_receipts_when_score_key_matches_inventory_item(self, monkeypatch):
        bills = [
            {
                "invoice_number": "INV-2",
                "supplier": "Test Supplier",
                "date": date(2026, 2, 20),
                "line_items": [
                    {"description": "12oz cups", "unit_amount": 20.0, "quantity": 10},
                ],
            }
        ]
        mapped = [
            {
                "description": "12oz cups",
                "score_key": "cup_12oz",
                "category": "ingredient",
                "unit_cost_cents": 2000,
                "confidence": "high",
                "line_quantity": 10,
                "units_per_pack": 100,
                "invoice_number": "INV-2",
                "supplier": "Test Supplier",
                "bill_date": date(2026, 2, 20),
                "line_index": 0,
            }
        ]
        receipts = []

        monkeypatch.setattr("data.xero.XeroClient", lambda _sid: _DummyClient(bills))
        monkeypatch.setattr("data.xero.map_xero_lines_to_score_keys", lambda _sid, _lines: mapped)
        monkeypatch.setattr(
            "data.xero.sync_xero_revenue",
            lambda *_args, **_kwargs: {
                "weeks_processed": 0,
                "weeks_reconciled": 0,
                "days_reconciled": 0,
            },
        )
        monkeypatch.setattr("data.xero.upsert_item_cost", lambda **_kwargs: None)
        monkeypatch.setattr(
            "data.xero.get_inventory_item_by_score_key",
            lambda *_args, **_kwargs: {"inventory_item_id": "inv-item-1"},
        )
        monkeypatch.setattr(
            "data.xero.store_inventory_receipt",
            lambda **kwargs: receipts.append(kwargs) or "receipt-1",
        )

        result = sync_xero_bills("site-1")

        assert result["inventory_receipts_linked"] == 1
        assert result["inventory_receipts_unmatched"] == 0
        assert len(receipts) == 1
        assert receipts[0]["quantity_units"] == 1000


class TestRevenueAllocation:
    def test_single_missing_day_gets_full_delta(self):
        missing = [date(2026, 2, 18)]
        allocations = _allocate_delta_to_missing_days(missing, 125000, {3: 200000})
        assert allocations == {date(2026, 2, 18): 125000}

    def test_multi_missing_days_use_dow_weights_and_preserve_total(self):
        missing = [date(2026, 2, 17), date(2026, 2, 18)]  # Tue, Wed
        # SQL DOW Tue=2, Wed=3
        dow_avgs = {2: 100000, 3: 300000}
        allocations = _allocate_delta_to_missing_days(missing, 40000, dow_avgs)
        assert sum(allocations.values()) == 40000
        assert allocations[date(2026, 2, 18)] > allocations[date(2026, 2, 17)]
