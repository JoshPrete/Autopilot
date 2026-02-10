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
├── voice_order/
│   ├── app.py             # FastAPI voice ordering sidecar
│   ├── catalog.py         # Catalog index + matching
│   ├── nlp.py             # Transcript parsing heuristics
│   └── square_service.py  # Square Orders + Terminal API helpers
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

## Spec Reference

Full specification: `Clubhouse Autopilot Spec v1.2 PRODUCTION.pdf`

---

Clubhouse Autopilot v1.2 | Clubhouse Coffee, Nundah QLD

---

## Voice Order Sidecar (POC)

This repo now includes a lightweight FastAPI service that parses voice transcripts
into candidate orders, then confirms via Square Orders API and (optionally) pushes
a Terminal checkout for in-person payment.

### Run

```bash
python scripts/voice_order_api.py
```

### Environment Variables

- `SQUARE_ACCESS_TOKEN`
- `SQUARE_LOCATION_ID`
- `SQUARE_ENVIRONMENT` (production or sandbox)
- `SQUARE_TERMINAL_DEVICE_ID` (optional; for checkout endpoint)
- `SQUARE_WEBHOOK_SIGNATURE_KEY` (optional; for webhook verification)
- `SQUARE_WEBHOOK_NOTIFICATION_URL` (optional; for webhook verification)
- `SQUARE_SANDBOX_TEST_SOURCE_ID` (optional; defaults to `cnon:card-nonce-ok`)

### Endpoints

- `POST /voice/parse` — parse transcript into proposed line items
- `POST /voice/confirm` — create Square order
- `POST /voice/checkout` — create Terminal checkout for an order
- `POST /webhooks/square` — receive Square webhooks (Terminal checkout updates)
- `GET /webhooks/recent` — view last 20 webhook events
- `POST /voice/mock-payment` — simulate a successful payment (no Square call)
- `POST /voice/pay-sandbox` — pay an order in Square Sandbox using a test source id
