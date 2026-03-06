import logging
import csv
import json
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app.routers import (
    analysis,
    auth,
    chat,
    delivery,
    documents,
    pipeline,
    predictions,
    recommendations,
    sites,
    tomorrow_plan,
    workload,
    xero,
)
from config.settings import settings

logger = logging.getLogger("autopilot.scheduler")

# ============================================================
# Scheduled Jobs
# ============================================================

scheduler = BackgroundScheduler(timezone="Australia/Brisbane")


def _resolve_site_id() -> tuple[str, str] | None:
    """Resolve site from SQUARE_LOCATION_ID for scheduled jobs."""
    from data.storage import get_site_by_location_id

    if not settings.SQUARE_LOCATION_ID:
        return None
    site = get_site_by_location_id(settings.SQUARE_LOCATION_ID)
    if site:
        return str(site["site_id"]), site["name"]
    return None


def _resolve_default_site_id() -> str | None:
    """Resolve a best-effort default site_id for UI routes."""
    from data.storage import get_site

    scheduled = _resolve_site_id()
    if scheduled:
        return scheduled[0]

    if settings.DEFAULT_SITE_ID:
        site = get_site(settings.DEFAULT_SITE_ID)
        if site:
            return str(site["site_id"])
    return None


def _derive_run_status(result: dict | None, fallback: str = "ok") -> str:
    if not isinstance(result, dict):
        return fallback
    step_status = (result.get("status") or "").lower()
    if step_status in ("error", "failed"):
        return "error"
    if step_status in ("skipped", "no_data", "dry_run"):
        return "skipped"
    if step_status == "ok":
        return "ok"
    return fallback


def _record_pipeline_run(
    site_id: str,
    job_name: str,
    started_at: datetime,
    result: dict | None = None,
    error_text: str | None = None,
    status: str | None = None,
) -> None:
    """Best-effort scheduler run observability."""
    from data.storage import store_pipeline_run

    final_status = status or _derive_run_status(result, fallback="ok")
    try:
        store_pipeline_run(
            site_id=site_id,
            job_name=job_name,
            status=final_status,
            started_at=started_at,
            finished_at=datetime.utcnow(),
            result=result,
            error_text=error_text,
        )
    except Exception:
        logger.exception("Failed to record pipeline run (%s)", job_name)


def scheduled_ingest():
    """5:00pm AEST — Ingest today's orders from Square."""
    from scripts.daily_autopilot import step_ingest

    site = _resolve_site_id()
    if not site:
        logger.warning("Scheduled ingest skipped: no site configured")
        return
    site_id, site_name = site
    started_at = datetime.utcnow()
    result = None
    error_text = None
    logger.info("=== SCHEDULED INGEST: %s (%s) ===", site_name, date.today())
    try:
        result = step_ingest(site_id, date.today())
        logger.info("Scheduled ingest complete: %s", result)
    except Exception as e:
        error_text = str(e)
        logger.exception("Scheduled ingest failed")
    finally:
        _record_pipeline_run(
            site_id=site_id,
            job_name="ingest",
            started_at=started_at,
            result=result,
            error_text=error_text,
            status="error" if error_text else None,
        )


def scheduled_deputy():
    """5:15pm AEST — Sync Deputy rosters (fail-quiet if not configured)."""
    from scripts.daily_autopilot import step_deputy

    site = _resolve_site_id()
    if not site:
        return
    site_id, _ = site
    started_at = datetime.utcnow()
    result = None
    error_text = None
    try:
        result = step_deputy(site_id, date.today())
        logger.info("Scheduled deputy sync: %s", result)
    except Exception as e:
        error_text = str(e)
        logger.exception("Scheduled deputy sync failed")
    finally:
        _record_pipeline_run(
            site_id=site_id,
            job_name="deputy",
            started_at=started_at,
            result=result,
            error_text=error_text,
            status="error" if error_text else None,
        )


def scheduled_profitability():
    """5:20pm AEST — Compute daily P&L (after deputy roster sync)."""
    from scripts.daily_autopilot import step_profitability

    site = _resolve_site_id()
    if not site:
        return
    site_id, _ = site
    started_at = datetime.utcnow()
    result = None
    error_text = None
    try:
        result = step_profitability(site_id, date.today())
        logger.info("Scheduled profitability: %s", result)
    except Exception as e:
        error_text = str(e)
        logger.exception("Scheduled profitability failed")
    finally:
        _record_pipeline_run(
            site_id=site_id,
            job_name="profitability",
            started_at=started_at,
            result=result,
            error_text=error_text,
            status="error" if error_text else None,
        )


def scheduled_xero_sync():
    """5:25pm AEST — Sync Xero supplier bills into item COGS (if connected)."""
    from data.xero import is_xero_configured, sync_xero_bills, sync_xero_revenue

    site = _resolve_site_id()
    if not site:
        return
    site_id, _ = site
    started_at = datetime.utcnow()
    result = None
    error_text = None

    try:
        if not is_xero_configured(site_id):
            logger.info("Scheduled Xero sync skipped: Xero not connected for site %s", site_id)
            result = {"status": "skipped", "reason": "not_configured"}
            return
        bills_result = sync_xero_bills(site_id, days_back=7)
        logger.info("Scheduled Xero bill sync: %s", bills_result)

        # Cross-check Square revenue against Xero P&L
        revenue_result = sync_xero_revenue(site_id, months_back=2)
        logger.info("Scheduled Xero revenue sync: %s", revenue_result)

        result = {"bills": bills_result, "revenue": revenue_result}
    except Exception as e:
        error_text = str(e)
        logger.exception("Scheduled Xero sync failed")
    finally:
        _record_pipeline_run(
            site_id=site_id,
            job_name="xero",
            started_at=started_at,
            result=result,
            error_text=error_text,
            status="error" if error_text else None,
        )


def scheduled_predict():
    """6:00pm AEST — Generate tomorrow's prediction and send SMS."""
    from scripts.daily_autopilot import step_predict

    site = _resolve_site_id()
    if not site:
        logger.warning("Scheduled predict skipped: no site configured")
        return
    site_id, site_name = site
    started_at = datetime.utcnow()
    result = None
    error_text = None
    tomorrow = date.today() + timedelta(days=1)
    logger.info("=== SCHEDULED PREDICT: %s (for %s) ===", site_name, tomorrow)
    try:
        result = step_predict(site_id, site_name, date.today())
        logger.info("Scheduled predict complete: %s", result)
    except Exception as e:
        error_text = str(e)
        logger.exception("Scheduled predict failed")
    finally:
        _record_pipeline_run(
            site_id=site_id,
            job_name="predict",
            started_at=started_at,
            result=result,
            error_text=error_text,
            status="error" if error_text else None,
        )


def scheduled_intelligence():
    """6:15pm AEST — Run daily intelligence cycle (after predict@6:00pm)."""
    from analysis.intelligence import run_intelligence_cycle

    site = _resolve_site_id()
    if not site:
        logger.warning("Scheduled intelligence skipped: no site configured")
        return
    site_id, site_name = site
    started_at = datetime.utcnow()
    result = None
    error_text = None
    try:
        result = run_intelligence_cycle(site_id, site_name, date.today())
        logger.info("Scheduled intelligence cycle: %s", result)
    except Exception as e:
        error_text = str(e)
        logger.exception("Scheduled intelligence cycle failed")
    finally:
        _record_pipeline_run(
            site_id=site_id,
            job_name="intelligence",
            started_at=started_at,
            result=result,
            error_text=error_text,
            status="error" if error_text else None,
        )


def scheduled_weekly_roi():
    """Monday 8:05am AEST — Send weekly ROI summary to manager."""
    from analysis.reporting import format_weekly_roi_sms, generate_weekly_roi_report
    from delivery.sender import send_to_manager

    site = _resolve_site_id()
    if not site:
        logger.warning("Scheduled weekly ROI skipped: no site configured")
        return

    site_id, site_name = site
    started_at = datetime.utcnow()
    result = None
    error_text = None
    try:
        report = generate_weekly_roi_report(site_id=site_id, site_name=site_name)
        sms_text = format_weekly_roi_sms(report)
        sms_results = send_to_manager(site_id, sms_text)
        delivered = sum(1 for r in sms_results if r.get("delivered"))
        result = {"status": "ok", "delivered": delivered, "total": len(sms_results)}
        logger.info("Scheduled weekly ROI SMS: %d/%d delivered", delivered, len(sms_results))
    except Exception as e:
        error_text = str(e)
        logger.exception("Scheduled weekly ROI failed")
    finally:
        _record_pipeline_run(
            site_id=site_id,
            job_name="weekly_roi",
            started_at=started_at,
            result=result,
            error_text=error_text,
            status="error" if error_text else None,
        )


def scheduled_weekly_kpi_snapshot():
    """Monday 8:00am AEST — Persist weekly KPI snapshot artifact."""
    from analysis.reporting import generate_pilot_kpi_snapshot

    site = _resolve_site_id()
    if not site:
        logger.warning("Scheduled KPI snapshot skipped: no site configured")
        return

    site_id, site_name = site
    started_at = datetime.utcnow()
    result = None
    error_text = None
    try:
        snapshot = generate_pilot_kpi_snapshot(site_id=site_id, site_name=site_name)

        base_dir = Path.cwd() / "analysis_outputs" / "pilot_kpi" / site_id
        base_dir.mkdir(parents=True, exist_ok=True)

        week_end = snapshot["week_end"]
        json_path = base_dir / f"{week_end}.json"
        json_path.write_text(json.dumps(snapshot, indent=2))

        csv_path = base_dir / "summary.csv"
        row = {
            "generated_at": snapshot.get("generated_at"),
            "site_id": site_id,
            "site_name": site_name,
            "week_start": snapshot.get("week_start"),
            "week_end": week_end,
            "labor_pct_avg": snapshot["business_kpis"].get("labor_pct_avg"),
            "revenue_per_labor_hour_avg_cents": snapshot["business_kpis"].get(
                "revenue_per_labor_hour_avg_cents"
            ),
            "weekly_net_profit_cents": snapshot["business_kpis"].get("weekly_net_profit_cents"),
            "weekly_revenue_cents": snapshot["business_kpis"].get("weekly_revenue_cents"),
            "net_profit_wow_delta_cents": snapshot["business_kpis"].get(
                "net_profit_wow_delta_cents"
            ),
            "labor_pct_wow_delta_pp": snapshot["business_kpis"].get("labor_pct_wow_delta_pp"),
            "rev_per_labor_hour_wow_delta_pct": snapshot["business_kpis"].get(
                "rev_per_labor_hour_wow_delta_pct"
            ),
            "avg_prediction_accuracy_pct": snapshot["ops_kpis"].get("avg_prediction_accuracy_pct"),
            "adoption_rate": snapshot["ops_kpis"].get("adoption_rate"),
            "pipeline_success_pct_estimate": snapshot["reliability"].get(
                "pipeline_success_pct_estimate"
            ),
        }
        write_header = not csv_path.exists()
        with csv_path.open("a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(row.keys()))
            if write_header:
                writer.writeheader()
            writer.writerow(row)

        result = {"status": "ok", "json_path": str(json_path), "csv_path": str(csv_path)}
        logger.info("Scheduled KPI snapshot written: %s", json_path)
    except Exception as e:
        error_text = str(e)
        logger.exception("Scheduled KPI snapshot failed")
    finally:
        _record_pipeline_run(
            site_id=site_id,
            job_name="weekly_kpi_snapshot",
            started_at=started_at,
            result=result,
            error_text=error_text,
            status="error" if error_text else None,
        )


# ============================================================
# App Lifecycle
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: validate environment
    startup_warnings = settings.validate_startup()
    if startup_warnings:
        for w in startup_warnings:
            logger.warning("CONFIG: %s", w)
    else:
        logger.info("CONFIG: all integrations configured")

    # Scheduler guard — skip if an external orchestrator owns the cron runs
    if settings.AUTOPILOT_DISABLE_SCHEDULER:
        logger.info(
            "Scheduler disabled (AUTOPILOT_DISABLE_SCHEDULER=true) — "
            "expecting external orchestrator to drive pipeline runs"
        )
        yield
        return

    # Startup: schedule daily jobs
    scheduler.add_job(
        scheduled_ingest,
        CronTrigger(hour=9, minute=0),
        id="morning_ingest",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_ingest,
        CronTrigger(hour=17, minute=0),
        id="daily_ingest",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_deputy,
        CronTrigger(hour=17, minute=15),
        id="daily_deputy",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_profitability,
        CronTrigger(hour=17, minute=20),
        id="daily_profitability",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_xero_sync,
        CronTrigger(hour=17, minute=25),
        id="daily_xero_sync",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_predict,
        CronTrigger(hour=18, minute=0),
        id="daily_predict",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_intelligence,
        CronTrigger(hour=18, minute=15),
        id="daily_intelligence",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_weekly_kpi_snapshot,
        CronTrigger(day_of_week="mon", hour=8, minute=0),
        id="weekly_kpi_snapshot",
        replace_existing=True,
    )
    scheduler.add_job(
        scheduled_weekly_roi,
        CronTrigger(day_of_week="mon", hour=8, minute=5),
        id="weekly_roi",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: ingest@09:00+17:00, deputy@17:15, profitability@17:20, "
        "xero@17:25, predict@18:00, intelligence@18:15, weekly_kpi@Mon08:00, weekly_roi@Mon08:05 AEST"
    )
    yield
    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("Scheduler stopped")


app = FastAPI(
    title="Clubhouse Autopilot API",
    description="REST API for Clubhouse Autopilot predictions, recommendations, and operations.",
    version="1.0.0",
    lifespan=lifespan,
)

_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard(request: Request):
    site_id = request.query_params.get("site_id")
    if not site_id:
        default_site_id = _resolve_default_site_id()
        if default_site_id:
            return RedirectResponse(url=f"/dashboard?site_id={default_site_id}", status_code=307)
    return (_static_dir / "dashboard.html").read_text()


@app.get("/dashboard", response_class=HTMLResponse, include_in_schema=False)
def dashboard_alias(request: Request):
    site_id = request.query_params.get("site_id")
    if not site_id:
        default_site_id = _resolve_default_site_id()
        if default_site_id:
            return RedirectResponse(url=f"/dashboard?site_id={default_site_id}", status_code=307)
    return (_static_dir / "dashboard.html").read_text()


@app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def chat_page(request: Request):
    site_id = request.query_params.get("site_id")
    if not site_id:
        default_site_id = _resolve_default_site_id()
        if default_site_id:
            return RedirectResponse(url=f"/chat?site_id={default_site_id}", status_code=307)
    return (_static_dir / "chat.html").read_text()


@app.get("/xero/setup", response_class=HTMLResponse, include_in_schema=False)
def xero_setup_page():
    return (_static_dir / "xero_setup.html").read_text()


@app.get("/health", tags=["health"])
def health_check():
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append(
            {
                "id": job.id,
                "next_run": str(job.next_run_time) if job.next_run_time else None,
            }
        )
    return {"status": "ok", "scheduled_jobs": jobs}


app.include_router(auth.router)
app.include_router(sites.router)
app.include_router(predictions.router)
app.include_router(recommendations.router)
app.include_router(workload.router)
app.include_router(pipeline.router)
app.include_router(delivery.router)
app.include_router(analysis.router)
app.include_router(tomorrow_plan.router)
app.include_router(chat.router)
app.include_router(documents.router)
app.include_router(xero.router)

# ============================================================
# React Frontend (served from built Vite output)
# ============================================================

_frontend_dir = Path(__file__).resolve().parent / "static" / "frontend"

if _frontend_dir.exists():
    app.mount(
        "/app/assets",
        StaticFiles(directory=str(_frontend_dir / "assets")),
        name="frontend-assets",
    )

    @app.get("/app/{rest_of_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def serve_frontend(rest_of_path: str = ""):
        return (_frontend_dir / "index.html").read_text()

else:

    @app.get("/app/{rest_of_path:path}", response_class=HTMLResponse, include_in_schema=False)
    def serve_frontend_dev(rest_of_path: str = ""):
        return HTMLResponse(
            content="<p>Frontend not built. Run <code>cd frontend && npm run build</code></p>",
            status_code=503,
        )
