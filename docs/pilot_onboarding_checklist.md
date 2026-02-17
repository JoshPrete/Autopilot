# Pilot Onboarding Checklist

## 1. Commercial Setup
- [ ] Pilot offer accepted (fees, scope, duration).
- [ ] Primary owner contact confirmed.
- [ ] Manager contact(s) confirmed for weekly check-ins.

## 2. Technical Access
- [ ] `DATABASE_URL` configured.
- [ ] `SQUARE_ACCESS_TOKEN` + `SQUARE_LOCATION_ID` configured.
- [ ] `DEPUTY_BASE_URL` + `DEPUTY_ACCESS_TOKEN` configured (if available).
- [ ] `TWILIO_*` credentials configured.
- [ ] `WEATHER_API_KEY` configured.
- [ ] `XERO_CLIENT_ID` + `XERO_CLIENT_SECRET` configured (if using Xero).

## 3. Site Setup
- [ ] Site exists in `sites` table and resolves via location ID.
- [ ] Contacts loaded for manager/staff routing.
- [ ] Timezone verified (`Australia/Brisbane` unless overridden).

## 4. Workflow Validation
- [ ] Daily ingest job runs successfully.
- [ ] Deputy sync runs successfully (or confirms fail-quiet mode).
- [ ] Profitability step writes to `daily_profitability`.
- [ ] Prediction step produces tomorrow forecast.
- [ ] Xero sync runs or cleanly skips when not connected.

## 5. Output Validation
- [ ] Dashboard loads with active site ID.
- [ ] Chat endpoint responds with site context.
- [ ] Weekly ROI endpoint returns data:
  - `/api/sites/{site_id}/analysis/weekly-roi`
- [ ] Weekly ROI script runs:
  - `python scripts/weekly_roi.py --site-id <SITE_UUID>`

## 6. Baseline Capture (Week 0)
- [ ] Baseline labor % recorded.
- [ ] Baseline revenue per labor hour recorded.
- [ ] Baseline weekly net profit recorded.
- [ ] Known constraints/events documented (closures, menu changes, staffing changes).

## 7. Operating Cadence
- [ ] Weekly review time scheduled with owner/manager.
- [ ] Incident/escalation contact path confirmed.
- [ ] Pilot KPI tracker initialized.
