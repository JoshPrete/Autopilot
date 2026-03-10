-- ============================================================
-- CLUBHOUSE AUTOPILOT v1.2 - Database Schema
-- PostgreSQL (production) / SQLite (dev)
-- ============================================================

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- Sites table
-- ============================================================
CREATE TABLE sites (
    site_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    name TEXT NOT NULL,
    square_location_id TEXT NOT NULL UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'Australia/Brisbane',
    created_at TIMESTAMP DEFAULT NOW(),
    active BOOLEAN DEFAULT TRUE
);

-- ============================================================
-- Contacts (Staff phone numbers for SMS routing)
-- ============================================================
CREATE TABLE contacts (
    contact_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    full_name TEXT NOT NULL,
    phone_e164 TEXT NOT NULL,
    role_label TEXT CHECK (role_label IN ('P1','P2','P3','MANAGER')),
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_contacts_site_role ON contacts(site_id, role_label) WHERE is_active = TRUE;

-- ============================================================
-- Menu Items (Workload mapping)
-- ============================================================
CREATE TABLE menu_items (
    item_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    catalog_item_id TEXT NOT NULL,
    item_name TEXT NOT NULL,
    category TEXT,
    base_workload_score REAL NOT NULL,
    avg_prep_seconds INT,
    active BOOLEAN DEFAULT TRUE,
    UNIQUE(site_id, catalog_item_id)
);

-- ============================================================
-- Modifiers
-- ============================================================
CREATE TABLE modifiers (
    modifier_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    catalog_modifier_id TEXT NOT NULL,
    modifier_name TEXT NOT NULL,
    workload_add REAL NOT NULL,
    UNIQUE(site_id, catalog_modifier_id)
);

-- ============================================================
-- Orders (Raw from Square)
-- ============================================================
CREATE TABLE orders_raw (
    order_id TEXT PRIMARY KEY,
    site_id UUID REFERENCES sites(site_id),
    created_at TIMESTAMP NOT NULL,
    closed_at TIMESTAMP,
    total_money_cents INT,
    state TEXT,
    payload JSONB NOT NULL,
    ingested_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_orders_site_created ON orders_raw(site_id, created_at);
CREATE INDEX idx_orders_site_closed ON orders_raw(site_id, closed_at);

-- ============================================================
-- Order Items
-- ============================================================
CREATE TABLE order_items (
    item_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    order_id TEXT REFERENCES orders_raw(order_id) ON DELETE CASCADE,
    site_id UUID REFERENCES sites(site_id),
    catalog_item_id TEXT,
    item_name TEXT,
    quantity INT DEFAULT 1,
    position_in_order INT,
    workload_units REAL NOT NULL,
    modifiers JSONB,
    created_at TIMESTAMP,
    completed_at TIMESTAMP,
    prep_time_seconds INT
);

CREATE INDEX idx_items_site_created ON order_items(site_id, created_at);

-- ============================================================
-- Workload Timeline (15-min aggregations)
-- ============================================================
CREATE TABLE workload_timeline (
    timeline_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    interval_start TIMESTAMP NOT NULL,
    workload_units REAL NOT NULL,
    orders_count INT NOT NULL,
    items_count INT NOT NULL,
    avg_prep_seconds REAL,
    calculated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(site_id, interval_start)
);

CREATE INDEX idx_workload_site_interval ON workload_timeline(site_id, interval_start);

-- ============================================================
-- Historical Pattern Analysis (Year-over-Year)
-- ============================================================
CREATE TABLE historical_patterns (
    pattern_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    pattern_type TEXT NOT NULL,  -- 'dow', 'yoy', 'event', 'seasonal'
    pattern_key TEXT NOT NULL,   -- 'monday', '2025-02-07', 'nundah_markets'
    avg_workload REAL NOT NULL,
    sample_size INT NOT NULL,    -- How many data points
    confidence REAL,             -- 0.0 to 1.0
    last_updated TIMESTAMP DEFAULT NOW(),
    UNIQUE(site_id, pattern_type, pattern_key)
);

CREATE INDEX idx_patterns_site_type ON historical_patterns(site_id, pattern_type);

-- ============================================================
-- Special Events Calendar
-- ============================================================
CREATE TABLE special_events (
    event_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    event_name TEXT NOT NULL,
    event_date DATE NOT NULL,
    event_type TEXT,        -- 'market', 'festival', 'holiday', 'school_holiday'
    recurrence TEXT,        -- 'weekly', 'annual', 'one_time'
    historical_impact REAL, -- Multiplier (1.0 = no impact, 1.18 = +18%)
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_events_site_date ON special_events(site_id, event_date);

-- ============================================================
-- Predictions (Updated with pattern breakdown)
-- ============================================================
CREATE TABLE predictions (
    prediction_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    forecast_date DATE NOT NULL,
    generated_at TIMESTAMP DEFAULT NOW(),
    model_version TEXT,

    -- Pattern components
    recent_baseline REAL,      -- Last 6-8 weeks
    yoy_baseline REAL,         -- Year-over-year
    dow_factor REAL,           -- Day-of-week multiplier
    event_factor REAL,         -- Special event multiplier

    -- Final forecast
    composite_baseline REAL,   -- Weighted combination
    forecast_data JSONB NOT NULL,
    rush_windows JSONB,
    confidence_score REAL,
    actual_accuracy REAL,
    UNIQUE(site_id, forecast_date)
);

CREATE INDEX idx_predictions_site_date ON predictions(site_id, forecast_date);

-- ============================================================
-- Recommendations (Daily actions)
-- ============================================================
CREATE TABLE recommendations (
    rec_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    prediction_id UUID REFERENCES predictions(prediction_id),
    site_id UUID REFERENCES sites(site_id),
    action_type TEXT NOT NULL,
    action_timing TIMESTAMP NOT NULL,
    owner_role TEXT CHECK (owner_role IN ('P1','P2','P3','MANAGER')),
    action_details JSONB,
    adopted BOOLEAN,
    outcome_data JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_recs_site_timing ON recommendations(site_id, action_timing);

-- ============================================================
-- Pipeline Runs (scheduler/API step observability)
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    job_name TEXT NOT NULL,
    status TEXT NOT NULL, -- ok, error, skipped
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    duration_ms INT,
    result_json JSONB,
    error_text TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_site_started ON pipeline_runs(site_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipeline_runs_site_job_started ON pipeline_runs(site_id, job_name, started_at DESC);

-- ============================================================
-- Data Quality Flags (strict fail-closed controls)
-- ============================================================
CREATE TABLE IF NOT EXISTS data_quality_flags (
    flag_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    flag_date DATE NOT NULL,
    flag_type TEXT NOT NULL, -- partial_ingest, manual_exclude_forecast
    severity TEXT NOT NULL DEFAULT 'medium',
    source TEXT NOT NULL DEFAULT 'system', -- system/manual
    reason TEXT,
    metadata JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    resolved_at TIMESTAMPTZ
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_data_quality_unique_active
ON data_quality_flags(site_id, flag_date, flag_type, source, active);
CREATE INDEX IF NOT EXISTS idx_data_quality_site_date ON data_quality_flags(site_id, flag_date DESC);

-- ============================================================
-- Adoption Logs (Daily feedback)
-- ============================================================
CREATE TABLE adoption_logs (
    log_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    log_date DATE NOT NULL,
    rec_id UUID REFERENCES recommendations(rec_id),
    manager_name TEXT,
    adopted BOOLEAN,
    rush_timing_rating INT CHECK (rush_timing_rating BETWEEN 1 AND 5),
    helpfulness_rating INT CHECK (helpfulness_rating BETWEEN 1 AND 5),
    notes TEXT,
    logged_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_adoption_site_date ON adoption_logs(site_id, log_date);

-- ============================================================
-- Manual Signals (Fallback toggles)
-- ============================================================
CREATE TABLE manual_signals (
    signal_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    signal_type TEXT NOT NULL,
    value TEXT,
    occurred_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_signals_site_time ON manual_signals(site_id, occurred_at);

-- ============================================================
-- KDS Tickets (Kitchen Performance CSV imports)
-- ============================================================
CREATE TABLE kds_tickets (
    ticket_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    device_name TEXT,
    ticket_name TEXT,                     -- customer name from POS
    order_source TEXT,                    -- 'Point of Sale', 'Square Online'
    num_items INT NOT NULL,
    items_description TEXT,               -- "1x 12oz/Medium; 1x 16oz/Large"
    completion_time_sec INT NOT NULL,     -- actual prep time in seconds
    time_created TIMESTAMP NOT NULL,
    time_completed TIMESTAMP NOT NULL,
    time_due TIMESTAMP,                   -- for online orders
    time_recalled TIMESTAMP,
    order_id TEXT REFERENCES orders_raw(order_id),  -- matched to orders_raw
    imported_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(site_id, time_created, items_description)
);

CREATE INDEX idx_kds_site_created ON kds_tickets(site_id, time_created);
CREATE INDEX idx_kds_order ON kds_tickets(order_id) WHERE order_id IS NOT NULL;
CREATE INDEX idx_kds_completion ON kds_tickets(site_id, completion_time_sec);

-- ============================================================
-- Daily Sales History (CSV imports + rolling API data)
-- ============================================================
CREATE TABLE daily_sales_history (
    history_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    sale_date DATE NOT NULL,
    gross_sales_cents INT,
    net_sales_cents INT,
    product_sales_cents INT,
    tax_cents INT,
    discounts_cents INT,
    tips_cents INT,
    total_collected_cents INT,
    orders_estimated INT,
    items_estimated INT,
    xero_revenue_cents INT,
    xero_synced_at TIMESTAMP,
    source TEXT DEFAULT 'csv',
    imported_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(site_id, sale_date)
);

CREATE INDEX idx_daily_sales_site_date ON daily_sales_history(site_id, sale_date);

-- ============================================================
-- Deputy Rosters (Workforce/shift data from Deputy)
-- ============================================================
CREATE TABLE IF NOT EXISTS deputy_rosters (
    id            SERIAL PRIMARY KEY,
    site_id       UUID NOT NULL REFERENCES sites(site_id),
    shift_date    DATE NOT NULL,
    start_time    TIMESTAMPTZ NOT NULL,
    end_time      TIMESTAMPTZ NOT NULL,
    employee_id   INTEGER,
    employee_name TEXT,
    total_hours   NUMERIC(5,2),
    cost_dollars  NUMERIC(8,2),
    is_published  BOOLEAN DEFAULT TRUE,
    is_open       BOOLEAN DEFAULT FALSE,
    deputy_id     INTEGER UNIQUE,
    created_at    TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_deputy_rosters_site_date ON deputy_rosters(site_id, shift_date);

-- ============================================================
-- Item Costs (COGS per menu item, keyed on score_key)
-- ============================================================
CREATE TABLE item_costs (
    cost_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    score_key TEXT NOT NULL,
    category TEXT NOT NULL,
    cost_cents INT NOT NULL,
    description TEXT,
    source TEXT DEFAULT 'default',  -- 'default' = seeded estimate, 'document' = from uploaded invoice
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(site_id, score_key)
);

-- ============================================================
-- Inventory Items (operational stock tracking)
-- ============================================================
CREATE TABLE inventory_items (
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
CREATE INDEX idx_inventory_items_site_active ON inventory_items(site_id, active);
CREATE UNIQUE INDEX idx_inventory_items_site_score_key
ON inventory_items(site_id, score_key)
WHERE score_key IS NOT NULL;

-- ============================================================
-- Inventory Usage Rules (sale -> stock consumption)
-- ============================================================
CREATE TABLE inventory_usage_rules (
    rule_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES sites(site_id),
    inventory_item_id UUID NOT NULL REFERENCES inventory_items(inventory_item_id) ON DELETE CASCADE,
    trigger_item_name TEXT NOT NULL,
    required_modifier_terms TEXT,  -- comma-separated terms; any term match required
    excluded_modifier_terms TEXT,  -- comma-separated terms; any term match blocks rule
    units_per_sale NUMERIC(12,4) NOT NULL,
    priority INT NOT NULL DEFAULT 100,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_inventory_usage_rules_site_active
ON inventory_usage_rules(site_id, active);
CREATE INDEX idx_inventory_usage_rules_item
ON inventory_usage_rules(inventory_item_id);
CREATE UNIQUE INDEX idx_inventory_usage_rules_unique
ON inventory_usage_rules(
    site_id,
    inventory_item_id,
    LOWER(trigger_item_name),
    COALESCE(LOWER(required_modifier_terms), ''),
    COALESCE(LOWER(excluded_modifier_terms), '')
);

-- ============================================================
-- Operator Rules (chat-confirmed operating knowledge)
-- ============================================================
CREATE TABLE IF NOT EXISTS operator_rules (
    rule_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES sites(site_id),
    rule_type TEXT NOT NULL,
    rule_name TEXT NOT NULL,
    payload JSONB NOT NULL,
    source TEXT NOT NULL DEFAULT 'chat',
    status TEXT NOT NULL DEFAULT 'proposed',
    confidence REAL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_by TEXT,
    confirmed_by TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    confirmed_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_operator_rules_site_status
ON operator_rules(site_id, status, active, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_operator_rules_site_type
ON operator_rules(site_id, rule_type, updated_at DESC);

-- ============================================================
-- Inventory Counts (physical count snapshots)
-- ============================================================
CREATE TABLE inventory_counts (
    count_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES sites(site_id),
    inventory_item_id UUID NOT NULL REFERENCES inventory_items(inventory_item_id) ON DELETE CASCADE,
    counted_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    quantity_on_hand NUMERIC(12,3) NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    notes TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX idx_inventory_counts_site_item_time
ON inventory_counts(site_id, inventory_item_id, counted_at DESC);

-- ============================================================
-- Inventory Receipts (stock received, e.g. from Xero bills)
-- ============================================================
CREATE TABLE inventory_receipts (
    receipt_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID NOT NULL REFERENCES sites(site_id),
    inventory_item_id UUID NOT NULL REFERENCES inventory_items(inventory_item_id) ON DELETE CASCADE,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    quantity_units NUMERIC(12,3) NOT NULL,
    unit_cost_cents INT,
    supplier_name TEXT,
    source TEXT NOT NULL DEFAULT 'xero',
    external_ref TEXT NOT NULL, -- idempotency key e.g. XERO:INV123:line2:itemX
    raw_line_description TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, external_ref)
);
CREATE INDEX idx_inventory_receipts_site_item_time
ON inventory_receipts(site_id, inventory_item_id, received_at DESC);

-- ============================================================
-- Daily Profitability (pre-computed daily P&L)
-- ============================================================
CREATE TABLE daily_profitability (
    profit_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    profit_date DATE NOT NULL,
    revenue_cents INT NOT NULL,
    labor_cost_cents INT NOT NULL,
    cogs_cents INT,
    gross_profit_cents INT,
    net_profit_cents INT,
    order_count INT,
    item_count INT,
    drink_count INT,
    labor_hours NUMERIC(6,2),
    revenue_per_labor_hour INT,
    cost_per_drink INT,
    labor_pct NUMERIC(5,2),
    labor_data_quality TEXT,
    labor_data_issues JSONB,
    computed_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(site_id, profit_date)
);

CREATE INDEX idx_profitability_site_date ON daily_profitability(site_id, profit_date);

-- ============================================================
-- Documents (uploaded invoices, receipts, CSVs)
-- ============================================================
CREATE TABLE documents (
    document_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    file_size_bytes INT NOT NULL,
    storage_path TEXT NOT NULL,
    document_type TEXT,              -- 'receipt', 'invoice', 'csv_pricelist', 'closure_note', 'other'
    extracted_data JSONB,
    extraction_summary TEXT,
    items_updated JSONB,
    uploaded_at TIMESTAMP DEFAULT NOW(),
    processed_at TIMESTAMP
);

CREATE INDEX idx_documents_site ON documents(site_id, uploaded_at DESC);

-- ============================================================
-- Xero OAuth2 Tokens (one row per site)
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_tokens (
    id            SERIAL PRIMARY KEY,
    site_id       UUID NOT NULL REFERENCES sites(site_id) UNIQUE,
    tenant_id     TEXT NOT NULL,
    access_token  TEXT NOT NULL, -- encrypted at rest
    refresh_token TEXT NOT NULL, -- encrypted at rest
    expires_at    TIMESTAMPTZ NOT NULL,
    scope         TEXT,
    connected_at  TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW()
);

-- ============================================================
-- Xero OAuth2 States (short-lived CSRF state for connect flow)
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_oauth_states (
    state      TEXT PRIMARY KEY,
    site_id    UUID NOT NULL REFERENCES sites(site_id),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_xero_oauth_states_expires ON xero_oauth_states(expires_at);

-- ============================================================
-- Xero Line Mappings (auditable proposal/approval workflow)
-- ============================================================
CREATE TABLE IF NOT EXISTS xero_line_mappings (
    id               SERIAL PRIMARY KEY,
    site_id          UUID NOT NULL REFERENCES sites(site_id),
    xero_description TEXT NOT NULL,
    score_key        TEXT NOT NULL,
    confidence       REAL CHECK (confidence >= 0 AND confidence <= 1),
    source           TEXT NOT NULL DEFAULT 'llm' CHECK (source IN ('human', 'llm', 'rule')),
    status           TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed', 'approved', 'rejected')),
    proposed_at      TIMESTAMPTZ DEFAULT NOW(),
    approved_at      TIMESTAMPTZ,
    approved_by      TEXT,
    model            TEXT,
    prompt_version   TEXT,
    units_per_pack   INTEGER DEFAULT 1 NOT NULL CHECK (units_per_pack >= 1),
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, xero_description)
);
CREATE INDEX IF NOT EXISTS idx_xero_line_mappings_site_status
ON xero_line_mappings(site_id, status, proposed_at DESC);

-- ============================================================
-- Xero Review Queue (operator triage)
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
-- Xero Cost History (for outlier guardrails)
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

-- ============================================================
-- Xero Financial Facts (daily factual cash in/out from Xero)
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
    completeness    TEXT NOT NULL DEFAULT 'partial', -- partial|full
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, fact_date)
);
CREATE INDEX IF NOT EXISTS idx_xero_financial_facts_site_date
ON xero_financial_facts(site_id, fact_date DESC);

-- ============================================================
-- Weekly Reviews
-- ============================================================
CREATE TABLE weekly_reviews (
    review_id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id UUID REFERENCES sites(site_id),
    week_start DATE NOT NULL,
    week_end DATE NOT NULL,
    avg_prediction_accuracy REAL,
    avg_adoption_rate REAL,
    labour_pct_actual REAL,
    labour_pct_target REAL,
    insights JSONB,
    actions_next_week JSONB,
    generated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(site_id, week_start)
);

-- ============================================================
-- Insights (Intelligence Engine observations)
-- ============================================================
CREATE TABLE insights (
    insight_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id         UUID NOT NULL REFERENCES sites(site_id),
    cycle_date      DATE NOT NULL,
    insight_type    TEXT NOT NULL,       -- 'staffing', 'margin', 'demand', 'prediction', 'revenue', 'operations'
    severity        TEXT NOT NULL DEFAULT 'info',  -- 'info', 'warning', 'opportunity'
    title           TEXT NOT NULL,
    body            TEXT NOT NULL,
    data            JSONB,              -- structured evidence (numbers, ranges, comparisons)
    action_type     TEXT,               -- recommended action_type for recommendations table (nullable)
    confidence      REAL DEFAULT 0.5,
    rec_id          UUID REFERENCES recommendations(rec_id),  -- linked recommendation if one was created
    status          TEXT DEFAULT 'active',  -- 'active', 'acted_on', 'dismissed', 'expired'
    expires_at      DATE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, cycle_date, insight_type, title)
);
CREATE INDEX idx_insights_site_date ON insights(site_id, cycle_date DESC);

-- ============================================================
-- Learned Patterns (Intelligence Engine memory)
-- ============================================================
CREATE TABLE learned_patterns (
    pattern_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    site_id         UUID NOT NULL REFERENCES sites(site_id),
    pattern_type    TEXT NOT NULL,       -- matches insight_type
    pattern_key     TEXT NOT NULL,       -- unique identifier e.g. 'overstaffed_tuesday_14'
    description     TEXT NOT NULL,       -- human-readable summary
    pattern_data    JSONB NOT NULL,      -- structured pattern: thresholds, values, evidence
    confidence      REAL DEFAULT 0.5,   -- 0.0-1.0, adjusted by outcomes
    sample_size     INT DEFAULT 1,
    total_impact_cents INT DEFAULT 0,   -- cumulative measured P&L impact
    last_validated  TIMESTAMPTZ,
    suppressed      BOOLEAN DEFAULT FALSE,  -- TRUE if consistently negative outcomes
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(site_id, pattern_type, pattern_key)
);
