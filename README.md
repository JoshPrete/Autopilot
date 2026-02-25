# Clubhouse Autopilot (Ops Spine)

Clubhouse Autopilot is an operations pipeline for cafe decision support.

It focuses on the daily spine:
- ingest trading + labor + accounting data
- compute profitability and workload pressure
- generate tomorrow staffing/ops plan
- deliver operator-facing alerts and plan output

## Requirements

- Python 3.11+
- PostgreSQL 14+
- Square API credentials
- Deputy credentials (optional, fail-quiet)
- Xero credentials (optional, fail-quiet; requires `AUTOPILOT_TOKEN_ENC_KEY` when enabled)
- Twilio credentials (optional, fail-quiet)
- OpenWeatherMap API key (optional, fail-quiet)
- Anthropic API key (optional, for intelligence LLM synthesis)

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Create schema:

```bash
psql "$DATABASE_URL" -f schema.sql
```

If upgrading an existing database, also run:

```bash
psql "$DATABASE_URL" -f scripts/migrations/2026-02-22_xero_controlled_enrichment.sql
```

## Tomorrow Habit CLI

Generate one operator report for tomorrow in under a minute:

```bash
make tomorrow
```

Optional arguments are passed via `ARGS`:

```bash
make tomorrow ARGS="--site-id <SITE_UUID> --date 2026-02-25"
```

Output file:
- `reports/tomorrow_YYYY-MM-DD.md`

Report sections:
1. Forecast revenue + confidence
2. Predicted rush windows + workload bands
3. Wage% risk flag (green/amber/red)
4. One recommended action

If data is missing or partial, the command fails loudly and prints copy/paste fix commands.

Verify accuracy and append one row to CSV:

```bash
make verify DATE=2026-02-26
```

Output file:
- `logs/accuracy.csv`

## Architecture

### Pipeline Steps

The daily pipeline runs 6 steps in sequence. Each step is independently runnable via `--step`.

```
5:00pm   INGEST ──────→ orders_raw, order_items, workload_timeline, daily_sales_history
5:15pm   DEPUTY ──────→ deputy_rosters
5:20pm   PROFITABILITY → daily_profitability  (reads: orders + rosters + item_costs)
5:25pm   XERO ────────→ item_costs, xero_financial_facts, daily_sales_history
6:00pm   PREDICT ─────→ predictions, recommendations  (sends: Tomorrow Plan SMS)
6:15pm   INTELLIGENCE → insights, learned_patterns     (sends: digest SMS)
```

| Step | Source | Writes To | Failure Mode |
|------|--------|-----------|--------------|
| `ingest` | Square API | `orders_raw`, `order_items`, `workload_timeline`, `daily_sales_history` | Fatal — blocks pipeline |
| `deputy` | Deputy API | `deputy_rosters` | Fail-quiet — logs warning, continues |
| `profitability` | DB (orders + rosters + Xero costs) | `daily_profitability` | Fail-quiet |
| `xero` | Xero API (OAuth) | `item_costs`, `xero_financial_facts`, `inventory_receipts` | Fail-quiet |
| `predict` | DB (historical patterns + events + weather) | `predictions`, `recommendations` + SMS | Fail-closed if data-quality flag active |
| `intelligence` | DB (profitability + patterns + insights) | `insights`, `learned_patterns`, `recommendations` + SMS | Fail-quiet |

### Data Flow

```
Square API ──→ INGEST ──→ workload_timeline ──┐
                  │                            │
                  └─→ daily_sales_history       │
                                               ▼
Deputy API ──→ DEPUTY ──→ deputy_rosters ──→ PROFITABILITY ──→ daily_profitability
                                               ▲                       │
Xero API ────→ XERO ───→ item_costs ──────────┘                       │
                  │                                                    │
                  └─→ xero_financial_facts                             ▼
                                                                 INTELLIGENCE
Historical patterns + special_events + weather ──→ PREDICT          │
         │                                            │              │
         │                                            ▼              ▼
         │                                     predictions      insights
         │                                     recommendations  learned_patterns
         │                                            │
         │                                            ▼
         │                                     Tomorrow Plan (stdout + SMS)
         │
         └──────────────────────────────────→ CHAT (always-on context)
```

### Data Sources

| System | Purpose | Credentials |
|--------|---------|-------------|
| Square API | Orders, items, revenue | `SQUARE_ACCESS_TOKEN`, `SQUARE_LOCATION_ID` |
| Deputy API | Rosters, labor hours, staff names | `DEPUTY_ACCESS_TOKEN`, `DEPUTY_BASE_URL` |
| Xero API | Bills (COGS), P&L revenue reconciliation | `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`, `AUTOPILOT_TOKEN_ENC_KEY` |
| OpenWeatherMap | Weather forecasts for demand adjustment | `WEATHER_API_KEY` |
| Anthropic (Claude) | Intelligence synthesis, chat | `ANTHROPIC_API_KEY` |
| Twilio | SMS delivery (plan, alerts, digest) | `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` |

### Key Database Tables

| Table | Role |
|-------|------|
| `orders_raw` | Raw Square order payloads |
| `order_items` | Parsed line items with workload_units per item |
| `workload_timeline` | 15-minute aggregated workload intervals |
| `daily_sales_history` | Daily revenue totals (Square + Xero columns) |
| `deputy_rosters` | Staff shifts: employee, hours, cost |
| `item_costs` | COGS per score_key (from Xero bills) |
| `daily_profitability` | Daily P&L: revenue, labor, COGS, net profit, labor % |
| `predictions` | Forecast output: layers, rush windows, confidence, plan snapshot |
| `recommendations` | Timed actions per role (P1/P2/P3/MANAGER) |
| `data_quality_flags` | Partial ingest markers — blocks predict step |
| `insights` | Intelligence observations with severity |
| `learned_patterns` | Recursive pattern memory with confidence + cumulative P&L impact |
| `special_events` | Market days, holidays, etc. (manual) |
| `xero_financial_facts` | Xero P&L revenue by date |

## Pipeline Steps — Detail

### Step 1: Ingest

Fetches completed orders from Square API, calculates workload scores using a 4-layer model:

1. Base drink scores (flat_white=1.0, latte=1.1, etc.)
2. Modifier adjustments (extra shot=+0.3, large milk=+0.2)
3. Multi-drink position penalties (2nd drink=x0.8, 3rd+=x0.7)
4. 15-minute timeline aggregation

Also updates yesterday's prediction accuracy (forecast vs actuals).

Applies **data-quality guard** — flags partial ingestions so the predict step is blocked.

### Step 2: Deputy Roster Sync

Fetches rosters for today + next 14 days from Deputy API. Enriches with employee names. Stored in `deputy_rosters` with shift hours and cost.

### Step 3: Profitability

Cross-correlates Square orders, Deputy labor costs, and Xero COGS to produce daily P&L:

- Revenue from order totals
- Labor cost from roster hours x rate
- COGS from `item_costs` x quantity sold
- Gross/net profit, labor % of revenue, revenue per labor hour

### Step 4: Xero Sync

Syncs supplier bills (COGS) and P&L revenue from Xero.

**Controlled enrichment model:**
- Approved mappings (`status=approved`) are always applied.
- LLM suggestions are stored as `status=proposed` with confidence + model metadata.
- Proposed mappings are not applied by default.
- Optional auto-apply requires both:
  - `ALLOW_AUTO_APPLY_PROPOSED_MAPPINGS=true`
  - `confidence >= MIN_CONFIDENCE_AUTO_APPLY`
- All applied costs pass guardrails:
  - Delta clamp vs current cost (`MAX_COST_DELTA_PCT`, default 40%)
  - IQR outlier check using `xero_cost_history` when history exists
- Blocked cases are quarantined into `xero_review_queue` with reason codes:
  - `UNMAPPED`, `LOW_CONFIDENCE`, `PENDING_APPROVAL`, `OUTLIER_COST`, `EXCESSIVE_DELTA`, `TOKEN_ERROR`

**Revenue reconciliation:** Weekly Xero P&L income vs Square known days. Delta allocated to missing/partial days using DOW-weighted historical patterns. Priority: Xero (verified) > Square CSV > Square API.

**GST handling:** All financial figures are ex-GST (true cash position). Xero P&L is already ex-GST. Square totals are divided by 1.10 to strip Australian GST.

Requires completed OAuth flow via `/xero/setup`. Manual trigger:

```bash
curl -X POST "http://localhost:8000/api/xero/sync?site_id=<SITE_UUID>"
```

Review queue CLI (DB-only, no Xero API calls):

```bash
.venv/bin/python scripts/xero_review.py --site-id <SITE_UUID> list --since 7d
.venv/bin/python scripts/xero_review.py --site-id <SITE_UUID> approve --mapping-id <ID> --score-key <score_key>
.venv/bin/python scripts/xero_review.py --site-id <SITE_UUID> reject --mapping-id <ID>
.venv/bin/python scripts/xero_review.py --site-id <SITE_UUID> apply --mapping-id <ID>
```

### Step 5: Predict

**Pre-flight:** Checks `data_quality_flags` for active partial_ingest. If found, **blocks prediction entirely** — sends alert SMS to manager, skips downstream plan/intelligence.

**Forecast:** 4-layer composite model:
- Layer 1 — Recent baseline: last 6-8 weeks same DOW average
- Layer 2 — Year-over-year: same date last year
- Layer 3 — Special events: multiplier from `special_events` table
- Layer 4 — Demand trends: recent pattern deviations

**Rush detection:** Identifies 2-4 hour windows where workload peaks. Each rush includes start/end time, predicted drinks, workload units, confidence.

**Recommendations:** State-machine-driven actions per role (P1/P2/P3/MANAGER).

**Tomorrow Plan:** Rendered to stdout (thermal-printer compatible, 59-char wide) and persisted to `predictions.plan_snapshot_text`. Sent via SMS to staff.

### Step 6: Intelligence

5-phase recursive engine:

1. **Measure** — Query past recommendation outcomes against profitability
2. **Learn** — Update pattern confidence scores (positive=+0.05, negative=-0.10, suppress if < 0.15)
3. **Observe** — Run signal detectors: staffing, efficiency gap, margins, demand, prediction accuracy, revenue, profitability, inventory
4. **Analyze** — LLM synthesis (Claude API) of signals into top 5 insights
5. **Recommend** — Convert warning/opportunity insights into actionable recommendations

High-severity insights sent as SMS digest to manager.

## Staffing Efficiency Gap Engine

Quantifies the dollar cost of overstaffing by comparing actual staff on shift against the minimum viable staff needed to maintain service quality.

**Core metrics:**
- `efficiency_score` = minimum required labor cost / actual labor cost (1.0 = perfect)
- `excess_labor_cents` = (actual_staff - min_viable_staff) x cost_per_interval
- Rollups by day and day-of-week

**Intelligence signals:**
- Overall low efficiency: triggers when score < 0.80
- Per-DOW recurring excess: triggers when a day averages > $13/day excess
- Efficiency trend: compares first/second halves of a 28-day window

**Chat visibility:** Always-visible 7-day efficiency summary. Ask "how efficient is our staffing?" for detailed breakdown.

## Chat System

Endpoint: `POST /api/sites/{site_id}/chat/message` (SSE streaming via Claude API).

Context loaded on every message:
- Latest prediction + today's ingest summary
- 7-day staffing efficiency (score, excess labor, worst DOW)
- 14-day profitability trend + item-level margins
- Recent insights + high-confidence learned patterns
- Deputy rosters (today + 14 days)
- Inventory alerts (if configured)
- Uploaded documents (OCR/PDF extracts)

## Data-Quality Guard

If ingest is flagged partial, the predict step is fail-closed:

- Blocks prediction entirely
- Prints operator message: reasons + copy/paste rerun commands
- Sends system alert to manager (`prediction_blocked_data_quality`)
- Downstream plan/SMS/intelligence all skipped

## Replan

Regenerate tomorrow's plan from stored prediction without recomputing forecast:

```bash
.venv/bin/python scripts/daily_autopilot.py --step replan --date 2026-02-20

# With updated staff names
.venv/bin/python scripts/daily_autopilot.py --step replan --date 2026-02-20 \
  --staff-names "P1:Sarah,P2:Tom"

# By specific prediction UUID
.venv/bin/python scripts/daily_autopilot.py --step predict --prediction-id <UUID>
```

## Scheduled Jobs

| Time (AEST) | Job | Description |
|-------------|-----|-------------|
| 9:00am | `scheduled_ingest()` | Morning ingest (Square orders so far) |
| 5:00pm | `scheduled_ingest()` | Full day ingest |
| 5:15pm | `scheduled_deputy()` | Deputy roster sync |
| 5:20pm | `scheduled_profitability()` | Daily P&L computation |
| 5:25pm | `scheduled_xero_sync()` | Xero bills + revenue sync |
| 6:00pm | `scheduled_predict()` | Forecast + Tomorrow Plan + SMS |
| 6:15pm | `scheduled_intelligence()` | Intelligence cycle + digest SMS |
| Monday 8:00am | `scheduled_weekly_kpi_snapshot()` | KPI snapshot to `analysis_outputs/` |
| Monday 8:05am | `scheduled_weekly_roi()` | Weekly ROI SMS to manager |

All jobs use APScheduler with `Australia/Brisbane` timezone.

## CLI Usage

```bash
# Full pipeline
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step all

# Individual steps
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step ingest
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step deputy
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step xero
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step profitability
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step predict
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step intelligence
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step replan

# Dry run (no SMS, no DB writes)
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step all --dry-run

# Specific date
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --date 2026-02-20

# With staff names
.venv/bin/python scripts/daily_autopilot.py --site-id <UUID> --step predict \
  --staff-names "P1:Sarah,P2:Tom,P3:Jessica"
```

## Formatting and Lint

```bash
make format    # black + ruff --fix
make lint      # ruff check + black --check
make check     # py_compile all entry points
make test      # pytest tests/ -x -q
```

Configured in `pyproject.toml`: Black (line-length=100) + Ruff (conservative E9/F rules).

Pre-commit hooks: `.pre-commit-config.yaml`.

## Key Paths

| Path | Purpose |
|------|---------|
| `scripts/daily_autopilot.py` | Primary daily pipeline runner (CLI + step functions) |
| `data/storage.py` | All DB operations, data quality controls, migrations |
| `data/ingestion.py` | Square API order fetching |
| `data/processing.py` | Workload score calculation (drink scores, modifiers) |
| `data/xero.py` | Xero API client + revenue reconciliation |
| `models/prediction.py` | 4-layer composite forecast model |
| `models/workload.py` | Workload engine (timeline aggregation) |
| `models/recommendations.py` | State machine recommendation generator |
| `analysis/intelligence.py` | 5-phase intelligence cycle |
| `analysis/accuracy.py` | Prediction vs actual comparison |
| `analysis/reporting.py` | Weekly ROI report generation |
| `delivery/tomorrow_plan.py` | Plan rendering (text + HTML) |
| `delivery/sender.py` | Twilio SMS dispatch + system alerts |
| `delivery/sms_prompts.py` | SMS template library |
| `app/main.py` | FastAPI app + APScheduler job registration |
| `app/chat.py` | Chat endpoint (SSE streaming, context gathering) |
| `config/constants.py` | Business logic constants (rates, thresholds, weights) |
| `config/workflow_profiles.py` | Workload-per-person thresholds, minimum viable staff |
| `config/settings.py` | Environment variable loading |
| `schema.sql` | PostgreSQL schema |

## Design Principles

1. **Fail-quiet** — Non-critical integrations (Deputy, Xero, Weather) never block the pipeline
2. **Fail-closed on data quality** — Partial ingestions block prediction entirely
3. **Deterministic regeneration** — Plans reproducible from stored prediction records
4. **Recursive learning** — Learned patterns gain/lose confidence from measured P&L outcomes
5. **Step atomicity** — Each step is independently runnable and idempotent
6. **Ex-GST financials** — All dollar figures are true cash position (GST is ATO pass-through)
