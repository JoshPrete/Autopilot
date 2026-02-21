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
- renders plan deterministically from persisted forecast/rush data
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

## Key Paths

- `scripts/daily_autopilot.py` - primary daily pipeline runner
- `data/storage.py` - persistence + data quality controls
- `delivery/tomorrow_plan.py` - plan rendering
- `delivery/sender.py` - SMS dispatch and system alerts
- `schema.sql` - database schema
