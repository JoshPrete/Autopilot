-- Clubhouse Autopilot migration
-- Date: 2026-02-20
--
-- Purpose:
-- 1) Ensure tables required by current API paths exist.
-- 2) Unblock scorecard/data-health/xero status endpoints on older DBs.
--
-- Run with a DB role that can CREATE in schema public.
-- Example:
--   psql "$DATABASE_URL" -f scripts/migrations/2026-02-20_bootstrap_missing_tables.sql

BEGIN;

DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
EXCEPTION WHEN insufficient_privilege THEN
    RAISE NOTICE 'Skipping CREATE EXTENSION uuid-ossp due to insufficient privileges';
END $$;

-- ============================================================
-- Data Quality Flags
-- ============================================================
CREATE TABLE IF NOT EXISTS data_quality_flags (
    flag_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    flag_date DATE NOT NULL,
    flag_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'medium',
    source TEXT NOT NULL DEFAULT 'system',
    reason TEXT,
    metadata JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_data_quality_unique_active
ON data_quality_flags(site_id, flag_date, flag_type, source, active);

CREATE INDEX IF NOT EXISTS idx_data_quality_site_date
ON data_quality_flags(site_id, flag_date DESC);

-- ============================================================
-- Insights
-- ============================================================
CREATE TABLE IF NOT EXISTS insights (
    insight_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id         UUID NOT NULL REFERENCES sites(site_id),
    cycle_date      DATE NOT NULL,
    insight_type    TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'info',
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    data            JSONB,
    action_type     TEXT,
    confidence      REAL DEFAULT 0.5,
    rec_id          UUID REFERENCES recommendations(rec_id),
    status          TEXT DEFAULT 'active',
    expires_at      DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, cycle_date, insight_type, title)
);

CREATE INDEX IF NOT EXISTS idx_insights_site_date
ON insights(site_id, cycle_date DESC);

-- ============================================================
-- Learned Patterns
-- ============================================================
CREATE TABLE IF NOT EXISTS learned_patterns (
    pattern_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id         UUID NOT NULL REFERENCES sites(site_id),
    pattern_type    TEXT NOT NULL,
    pattern_key     TEXT NOT NULL,
    description     TEXT NOT NULL,
    pattern_data    JSONB NOT NULL,
    confidence      REAL DEFAULT 0.5,
    sample_size     INT DEFAULT 1,
    total_impact_cents INT DEFAULT 0,
    last_validated  TIMESTAMPTZ,
    suppressed      BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, pattern_type, pattern_key)
);

-- ============================================================
-- Xero Financial Facts
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_financial_facts (
    fact_id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id         UUID NOT NULL REFERENCES sites(site_id),
    fact_date       DATE NOT NULL,
    income_cents    INT NOT NULL DEFAULT 0,
    expense_cents   INT NOT NULL DEFAULT 0,
    payroll_cents   INT,
    net_cash_cents  INT NOT NULL DEFAULT 0,
    txn_count       INT NOT NULL DEFAULT 0,
    source          TEXT NOT NULL DEFAULT 'xero_bank_transactions',
    completeness    TEXT NOT NULL DEFAULT 'partial',
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, fact_date)
);

CREATE INDEX IF NOT EXISTS idx_xero_financial_facts_site_date
ON xero_financial_facts(site_id, fact_date DESC);

-- ============================================================
-- Daily Sales History Xero Cross-Check Columns
-- ============================================================
ALTER TABLE daily_sales_history
    ADD COLUMN IF NOT EXISTS xero_revenue_cents INT;

ALTER TABLE daily_sales_history
    ADD COLUMN IF NOT EXISTS xero_synced_at TIMESTAMP;

COMMIT;
