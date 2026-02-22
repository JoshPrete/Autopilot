-- Clubhouse Autopilot migration
-- Date: 2026-02-22
--
-- Purpose:
-- 1) Add inventory stock model (items, usage rules, counts, receipts).
-- 2) Enable low-stock alerts and Xero receipt imports.
--
-- Run:
--   psql "$DATABASE_URL" -f scripts/migrations/2026-02-22_inventory_stock_alerts.sql

BEGIN;

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'Skipping CREATE EXTENSION uuid-ossp due to insufficient privileges';
END $$;

-- ============================================================
-- Inventory Items
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory_items (
    inventory_item_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES sites(site_id),
    item_name TEXT NOT NULL,
    score_key TEXT,
    unit TEXT NOT NULL DEFAULT 'units',
    reorder_point NUMERIC(12,3) NOT NULL DEFAULT 0,
    par_level NUMERIC(12,3),
    lead_time_days INT NOT NULL DEFAULT 2,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    metadata JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, item_name)
);

CREATE INDEX IF NOT EXISTS idx_inventory_items_site_active
ON inventory_items(site_id, active);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_items_site_score_key
ON inventory_items(site_id, score_key)
WHERE score_key IS NOT NULL;

-- ============================================================
-- Inventory Usage Rules
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory_usage_rules (
    rule_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES sites(site_id),
    inventory_item_id UUID NOT NULL REFERENCES inventory_items(inventory_item_id) ON DELETE CASCADE,
    trigger_item_name TEXT NOT NULL,
    required_modifier_terms TEXT,
    excluded_modifier_terms TEXT,
    units_per_sale NUMERIC(12,4) NOT NULL,
    priority INT NOT NULL DEFAULT 100,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inventory_usage_rules_site_active
ON inventory_usage_rules(site_id, active);

CREATE INDEX IF NOT EXISTS idx_inventory_usage_rules_item
ON inventory_usage_rules(inventory_item_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_inventory_usage_rules_unique
ON inventory_usage_rules(
    site_id,
    inventory_item_id,
    LOWER(trigger_item_name),
    COALESCE(LOWER(required_modifier_terms), ''),
    COALESCE(LOWER(excluded_modifier_terms), '')
);

-- ============================================================
-- Inventory Counts
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory_counts (
    count_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES sites(site_id),
    inventory_item_id UUID NOT NULL REFERENCES inventory_items(inventory_item_id) ON DELETE CASCADE,
    counted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    quantity_on_hand NUMERIC(12,3) NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_inventory_counts_site_item_time
ON inventory_counts(site_id, inventory_item_id, counted_at DESC);

-- ============================================================
-- Inventory Receipts
-- ============================================================
CREATE TABLE IF NOT EXISTS inventory_receipts (
    receipt_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES sites(site_id),
    inventory_item_id UUID NOT NULL REFERENCES inventory_items(inventory_item_id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    quantity_units NUMERIC(12,3) NOT NULL,
    unit_cost_cents INT,
    supplier_name TEXT,
    source TEXT NOT NULL DEFAULT 'xero',
    external_ref TEXT NOT NULL,
    raw_line_description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, external_ref)
);

CREATE INDEX IF NOT EXISTS idx_inventory_receipts_site_item_time
ON inventory_receipts(site_id, inventory_item_id, received_at DESC);

COMMIT;
