-- Clubhouse Autopilot migration
-- Date: 2026-02-22
--
-- Purpose:
-- 1) Add auditable mapping workflow fields for Xero line mappings.
-- 2) Add operator review queue and cost history tables.
-- 3) Preserve legacy confidence values while converting to numeric confidence.
--
-- Run:
--   psql "$DATABASE_URL" -f scripts/migrations/2026-02-22_xero_controlled_enrichment.sql

BEGIN;

-- ============================================================
-- xero_line_mappings: workflow columns + confidence conversion
-- ============================================================
ALTER TABLE xero_line_mappings
    ADD COLUMN IF NOT EXISTS source TEXT,
    ADD COLUMN IF NOT EXISTS status TEXT,
    ADD COLUMN IF NOT EXISTS proposed_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS approved_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS approved_by TEXT,
    ADD COLUMN IF NOT EXISTS model TEXT,
    ADD COLUMN IF NOT EXISTS prompt_version TEXT,
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ,
    ADD COLUMN IF NOT EXISTS units_per_pack INTEGER DEFAULT 1;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM information_schema.columns
        WHERE table_name = 'xero_line_mappings'
          AND column_name = 'confidence'
          AND data_type IN ('character varying', 'text')
    ) THEN
        ALTER TABLE xero_line_mappings ADD COLUMN IF NOT EXISTS confidence_real REAL;

        UPDATE xero_line_mappings
        SET confidence_real = CASE
            WHEN LOWER(COALESCE(confidence, '')) IN ('high', 'confirmed') THEN 0.95
            WHEN LOWER(COALESCE(confidence, '')) = 'medium' THEN 0.70
            WHEN LOWER(COALESCE(confidence, '')) = 'low' THEN 0.30
            WHEN confidence ~ '^[0-9]+(\\.[0-9]+)?$' THEN GREATEST(0, LEAST(1, confidence::REAL))
            ELSE 0.0
        END
        WHERE confidence_real IS NULL;

        ALTER TABLE xero_line_mappings DROP COLUMN confidence;
        ALTER TABLE xero_line_mappings RENAME COLUMN confidence_real TO confidence;
    END IF;
END $$;

ALTER TABLE xero_line_mappings
    ALTER COLUMN confidence TYPE REAL USING confidence::REAL,
    ALTER COLUMN source SET DEFAULT 'llm',
    ALTER COLUMN status SET DEFAULT 'proposed',
    ALTER COLUMN proposed_at SET DEFAULT NOW(),
    ALTER COLUMN updated_at SET DEFAULT NOW(),
    ALTER COLUMN units_per_pack SET DEFAULT 1;

UPDATE xero_line_mappings
SET source = COALESCE(NULLIF(source, ''), 'llm'),
    status = COALESCE(NULLIF(status, ''), 'proposed'),
    proposed_at = COALESCE(proposed_at, created_at, NOW()),
    updated_at = COALESCE(updated_at, NOW()),
    units_per_pack = GREATEST(1, COALESCE(units_per_pack, 1)),
    confidence = CASE
        WHEN confidence IS NULL THEN 0.0
        WHEN confidence < 0 THEN 0.0
        WHEN confidence > 1 THEN 1.0
        ELSE confidence
    END;

ALTER TABLE xero_line_mappings
    ALTER COLUMN source SET NOT NULL,
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN proposed_at SET NOT NULL,
    ALTER COLUMN updated_at SET NOT NULL,
    ALTER COLUMN units_per_pack SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'xero_line_mappings_source_check'
    ) THEN
        ALTER TABLE xero_line_mappings
            ADD CONSTRAINT xero_line_mappings_source_check
            CHECK (source IN ('human', 'llm', 'rule'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'xero_line_mappings_status_check'
    ) THEN
        ALTER TABLE xero_line_mappings
            ADD CONSTRAINT xero_line_mappings_status_check
            CHECK (status IN ('proposed', 'approved', 'rejected'));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'xero_line_mappings_confidence_check'
    ) THEN
        ALTER TABLE xero_line_mappings
            ADD CONSTRAINT xero_line_mappings_confidence_check
            CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1));
    END IF;

    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'xero_line_mappings_units_per_pack_check'
    ) THEN
        ALTER TABLE xero_line_mappings
            ADD CONSTRAINT xero_line_mappings_units_per_pack_check
            CHECK (units_per_pack >= 1);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_xero_line_mappings_site_status
ON xero_line_mappings(site_id, status, proposed_at DESC);

-- ============================================================
-- xero_review_queue
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_review_queue (
    review_id               SERIAL PRIMARY KEY,
    site_id                 UUID NOT NULL REFERENCES sites(site_id),
    queue_status            TEXT NOT NULL DEFAULT 'open' CHECK (queue_status IN ('open', 'resolved')),
    reason_code             TEXT NOT NULL,
    invoice_id              TEXT,
    invoice_number          TEXT,
    supplier                TEXT,
    line_description        TEXT NOT NULL,
    line_quantity           NUMERIC,
    unit_amount             NUMERIC,
    line_total              NUMERIC,
    bill_date               DATE,
    suggested_score_key     TEXT,
    suggested_confidence    REAL,
    mapping_id              INTEGER REFERENCES xero_line_mappings(id),
    payload                 JSONB,
    resolution_note         TEXT,
    resolved_by             TEXT,
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    resolved_at             TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_xero_review_queue_site_status
ON xero_review_queue(site_id, queue_status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_xero_review_queue_reason
ON xero_review_queue(site_id, reason_code, created_at DESC);

-- ============================================================
-- xero_cost_history
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_cost_history (
    history_id      SERIAL PRIMARY KEY,
    site_id         UUID NOT NULL REFERENCES sites(site_id),
    score_key       TEXT NOT NULL,
    cost_cents      INT NOT NULL,
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    source          TEXT NOT NULL DEFAULT 'xero',
    reference       TEXT
);

CREATE INDEX IF NOT EXISTS idx_xero_cost_history_site_key_time
ON xero_cost_history(site_id, score_key, observed_at DESC);

COMMIT;

