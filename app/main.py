from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.routers import (
    analysis,
    delivery,
    pipeline,
    predictions,
    recommendations,
    sites,
    tomorrow_plan,
    workload,
)

app = FastAPI(
    title="Clubhouse Autopilot API",
    description="REST API for Clubhouse Autopilot predictions, recommendations, and operations.",
    version="1.0.0",
)

_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def dashboard():
    return (_static_dir / "dashboard.html").read_text()


@app.get("/health", tags=["health"])
def health_check():
    return {"status": "ok"}


app.include_router(sites.router)
app.include_router(predictions.router)
app.include_router(recommendations.router)
app.include_router(workload.router)
app.include_router(pipeline.router)
app.include_router(delivery.router)
app.include_router(analysis.router)
app.include_router(tomorrow_plan.router)
