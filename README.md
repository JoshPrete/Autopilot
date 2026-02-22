# Clubhouse Autopilot (Ops Spine)

Clubhouse Autopilot is an operations pipeline for cafe decision support.

It focuses on the daily spine:
- ingest trading + labor + accounting data
- compute profitability and workload pressure
- generate tomorrow staffing/ops plan
- deliver operator-facing alerts and plan output

Voice ordering has been removed from this repository.

## Requirements

- Python 3.11+
- PostgreSQL 14+
- Square API credentials
- Deputy credentials (optional)
- Xero credentials (optional)
- Twilio credentials (optional for SMS)

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

## Daily Runner

Show CLI:

```bash
.venv/bin/python scripts/daily_autopilot.py --help
```

Run full pipeline:

```bash
.venv/bin/python scripts/daily_autopilot.py --site-id <SITE_UUID> --step all
```

Run dry-run:

```bash
.venv/bin/python scripts/daily_autopilot.py --site-id <SITE_UUID> --step all --dry-run
```

## Deterministic Tomorrow Plan Regeneration

Regenerate a previously stored tomorrow plan from a prediction record without recalculating forecast/recommendations:

```bash
.venv/bin/python scripts/daily_autopilot.py \
  --site-id <SITE_UUID> \
  --step predict \
  --prediction-id <PREDICTION_UUID>
```

Behavior:
- uses stored `predictions` row by `prediction_id`
- prefers an exact persisted plan snapshot when available
- otherwise renders from persisted forecast/rush data
- does not re-run model forecast or recommendation generation

## Operator Data-Quality Guard

If ingest is flagged partial, the predict step is blocked and emits:
- impacted site and date
- blocking reason(s)
- copy/paste rerun commands
- explicit note that downstream plan/SMS/intelligence were skipped

Alert type used for manager escalation: `prediction_blocked_data_quality`.

## Formatting and Lint

Use helper scripts:

```bash
./scripts/format.sh
./scripts/lint.sh
```

Or Make targets:

```bash
make format
make lint
```

Configured in `pyproject.toml`:
- Black (formatting)
- Ruff (lint)

## Xero Revenue Sync

Xero is the source of truth for verified revenue. The system reconciles Xero P&L income against Square POS data weekly.

**Revenue priority:** Xero (verified, ex-GST) > Square CSV (stripped to ex-GST) > Square API (stripped to ex-GST)

**GST handling:** All financial figures are ex-GST (true cash position). Xero P&L is already ex-GST. Square totals are divided by 1.10 to strip Australian GST.

**Scheduled sync:** Runs daily at 5:25pm AEST via `scheduled_xero_sync()` in `app/main.py`. Syncs both bills and revenue.

**Manual trigger:**

```bash
# Sync last 2 months (default)
curl -X POST http://localhost:8080/api/xero/sync-revenue

# Sync last 4 months
curl -X POST http://localhost:8080/api/xero/sync-revenue?months_back=4
```

**Reconciliation approach:**
1. Fetch weekly Xero P&L income totals (ex-GST)
2. Sum Square known days for the same week (converted to ex-GST)
3. Allocate delta to missing/partial days using DOW-weighted historical patterns
4. Store `xero_revenue_cents` and `xero_synced_at` on `daily_sales_history`

Requires `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET`, and a completed OAuth flow via `/api/xero/auth`.

## Staffing Efficiency Gap Engine

Quantifies the dollar cost of overstaffing by comparing actual staff on shift against the minimum viable staff needed to maintain service quality.

**Core metrics:**
- `efficiency_score` = minimum required labor cost / actual labor cost (1.0 = perfect)
- `excess_labor_cents` = (actual_staff - min_viable_staff) x cost_per_interval
- Rollups by day and day-of-week

**Intelligence signals** (generated in `analysis/intelligence.py`):
- Overall low efficiency: triggers when score < 0.80
- Per-DOW recurring excess: triggers when a day averages > $13/day excess
- Efficiency trend: compares first/second halves of a 28-day window

**Chat visibility:** Always-visible 7-day efficiency summary in the chat prompt. Ask "how efficient is our staffing?" for detailed breakdown.

## Replan (Deterministic Plan Regeneration)

Regenerate tomorrow's plan from the most recent stored prediction without recomputing the forecast:

```bash
.venv/bin/python scripts/daily_autopilot.py --step replan --date 2026-02-20

# With updated staff names
.venv/bin/python scripts/daily_autopilot.py --step replan --date 2026-02-20 \
  --staff-names "P1:Sarah,P2:Tom"
```

Looks up the latest prediction for `run_date + 1 day` and renders the plan. No model forecast or recommendation generation occurs.

Alternatively, use `--prediction-id` for a specific prediction UUID:

```bash
.venv/bin/python scripts/daily_autopilot.py --step predict --prediction-id <UUID>
```

## Scheduled Jobs

| Time (AEST) | Job | Description |
|-------------|-----|-------------|
| 5:00pm | `daily_autopilot.py --step ingest` | Pull today's Square orders |
| 5:25pm | `scheduled_xero_sync()` | Sync Xero bills + revenue |
| 6:00pm | `daily_autopilot.py --step all` | Full pipeline: predict + plan + SMS |
| 8:30am | Rush reminder | Pre-rush SMS to staff |
| Monday 8am | Weekly review | Weekly performance report |

## Key Paths

- `scripts/daily_autopilot.py` - primary daily pipeline runner
- `data/storage.py` - persistence + data quality controls
- `delivery/tomorrow_plan.py` - plan rendering
- `delivery/sender.py` - SMS dispatch and system alerts
- `schema.sql` - database schema
