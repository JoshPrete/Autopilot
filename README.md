# Clubhouse Autopilot v1.2

Espresso-native workflow control system for Clubhouse Coffee.

Sits above Square, Deputy, and existing platforms as a **state + priority enforcement layer**, converting order flow patterns, staffing configuration, and rush pressure signals into operational prompts.

## Core Outputs

1. **Tomorrow Plan** - Printed sheet for bench (daily 6pm)
2. **Shift Nudges** - SMS to manager/staff (real-time)
3. **Weekly Ops Review** - Performance metrics (Monday 8am)

## Setup

### Prerequisites

- Python 3.10+
- PostgreSQL 14+
- Square account with API access
- Twilio account for SMS
- OpenWeatherMap API key

### Installation

```bash
# Clone and enter project
cd clubhouse-autopilot

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your credentials

# Create database
createdb clubhouse_autopilot
psql clubhouse_autopilot < schema.sql

# Verify setup
python scripts/test_ingestion.py
```

### Environment Variables

See `.env.example` for all required variables. At minimum you need:

- `SQUARE_ACCESS_TOKEN` / `SQUARE_LOCATION_ID`
- `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_PHONE_NUMBER`
- `DATABASE_URL`
- `WEATHER_API_KEY`
- `MANAGER_PHONE`

## Project Structure

```
clubhouse-autopilot/
├── config/
│   ├── settings.py        # Environment variable loading
│   ├── database.py        # SQLAlchemy engine + session
│   └── constants.py       # Immutable policy values
├── data/
│   ├── ingestion.py       # Square API order fetching
│   ├── processing.py      # Workload score calculation
│   └── storage.py         # Database write operations
├── models/
│   ├── workload.py        # Workload engine logic
│   ├── prediction.py      # Multi-layer forecast model
│   └── recommendations.py # State machine + actions
├── delivery/
│   ├── tomorrow_plan.py   # PDF generation
│   ├── sms_prompts.py     # SMS template library
│   └── sender.py          # Twilio SMS dispatch
├── analysis/
│   ├── accuracy.py        # Prediction vs actual comparison
│   └── reporting.py       # Weekly review generation
├── scripts/
│   ├── daily_autopilot.py # Main daily pipeline (6pm cron)
│   └── weekly_review.py   # Weekly report (Monday 8am cron)
├── tests/
├── schema.sql             # PostgreSQL database schema
├── requirements.txt       # Python dependencies
├── .env.example           # Environment variable template
└── README.md
```

## Daily Operations

| Time | Job | Description |
|------|-----|-------------|
| 5:00pm | `ingest_daily.sh` | Pull today's Square orders |
| 6:00pm | `generate_plan.sh` | Generate Tomorrow Plan + SMS |
| 8:30am | `rush_reminder.sh` | Pre-rush SMS reminder |
| Monday 8am | `weekly_review.sh` | Weekly performance report |

## CLI Usage

```bash
# Full pipeline (default)
python scripts/daily_autopilot.py --date 2026-02-20

# Individual steps
python scripts/daily_autopilot.py --step ingest --date 2026-02-20
python scripts/daily_autopilot.py --step predict --date 2026-02-20
python scripts/daily_autopilot.py --step intelligence --date 2026-02-20

# Regenerate tomorrow plan from stored prediction (no recomputation)
python scripts/daily_autopilot.py --step replan --date 2026-02-20
python scripts/daily_autopilot.py --step replan --date 2026-02-20 --staff-names "P1:Sarah,P2:Tom"

# Dry run (no SMS, no DB writes)
python scripts/daily_autopilot.py --date 2026-02-20 --dry-run
```

Steps: `ingest` → `deputy` → `xero` → `profitability` → `predict` → `intelligence` | `replan` (standalone)

## Spec Reference

Full specification: `Clubhouse Autopilot Spec v1.2 PRODUCTION.pdf`

---

Clubhouse Autopilot v1.2 | Clubhouse Coffee, Nundah QLD

---

> Voice ordering POC has been moved to a separate repository.
