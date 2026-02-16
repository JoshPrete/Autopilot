import logging
from contextlib import asynccontextmanager
from datetime import date, timedelta
from pathlib import Path

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.routers import (
    analysis,
    chat,
    delivery,
    documents,
    pipeline,
    predictions,
    recommendations,
    sites,
    tomorrow_plan,
    workload,
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


def scheduled_ingest():
    """5:00pm AEST — Ingest today's orders from Square."""
    from scripts.daily_autopilot import step_ingest
    site = _resolve_site_id()
    if not site:
        logger.warning("Scheduled ingest skipped: no site configured")
        return
    site_id, site_name = site
    logger.info("=== SCHEDULED INGEST: %s (%s) ===", site_name, date.today())
    try:
        result = step_ingest(site_id, date.today())
        logger.info("Scheduled ingest complete: %s", result)
    except Exception:
        logger.exception("Scheduled ingest failed")


def scheduled_deputy():
    """5:15pm AEST — Sync Deputy rosters (fail-quiet if not configured)."""
    from scripts.daily_autopilot import step_deputy
    site = _resolve_site_id()
    if not site:
        return
    site_id, _ = site
    try:
        result = step_deputy(site_id, date.today())
        logger.info("Scheduled deputy sync: %s", result)
    except Exception:
        logger.exception("Scheduled deputy sync failed")


def scheduled_profitability():
    """5:20pm AEST — Compute daily P&L (after deputy roster sync)."""
    from scripts.daily_autopilot import step_profitability
    site = _resolve_site_id()
    if not site:
        return
    site_id, _ = site
    try:
        result = step_profitability(site_id, date.today())
        logger.info("Scheduled profitability: %s", result)
    except Exception:
        logger.exception("Scheduled profitability failed")


def scheduled_predict():
    """6:00pm AEST — Generate tomorrow's prediction and send SMS."""
    from scripts.daily_autopilot import step_predict
    site = _resolve_site_id()
    if not site:
        logger.warning("Scheduled predict skipped: no site configured")
        return
    site_id, site_name = site
    tomorrow = date.today() + timedelta(days=1)
    logger.info("=== SCHEDULED PREDICT: %s (for %s) ===", site_name, tomorrow)
    try:
        result = step_predict(site_id, site_name, date.today())
        logger.info("Scheduled predict complete: %s", result)
    except Exception:
        logger.exception("Scheduled predict failed")


# ============================================================
# App Lifecycle
# ============================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
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
        scheduled_predict,
        CronTrigger(hour=18, minute=0),
        id="daily_predict",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "Scheduler started: ingest@09:00+17:00, deputy@17:15, profitability@17:20, predict@18:00 AEST"
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
def dashboard():
    return (_static_dir / "dashboard.html").read_text()


@app.get("/chat", response_class=HTMLResponse, include_in_schema=False)
def chat_page():
    return (_static_dir / "chat.html").read_text()


@app.get("/health", tags=["health"])
def health_check():
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return {"status": "ok", "scheduled_jobs": jobs}


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
