from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import text

from app.dependencies import get_validated_site
from app.schemas import WorkloadRequest
from config.database import engine
from data.processing import calculate_order_workload

router = APIRouter(prefix="/api/sites/{site_id}/workload", tags=["workload"])


@router.post("/calculate")
def calculate_workload(body: WorkloadRequest, site: dict = Depends(get_validated_site)):
    parsed_order = body.order.model_dump()
    result = calculate_order_workload(parsed_order)
    return result


@router.get("/timeline")
def get_timeline(
    site: dict = Depends(get_validated_site),
    limit: int = Query(default=96, ge=1, le=500),
):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT * FROM workload_timeline "
                "WHERE site_id = :site_id "
                "ORDER BY window_start DESC LIMIT :limit"
            ),
            {"site_id": site["site_id"], "limit": limit},
        ).mappings().all()
    return [dict(r) for r in rows]
