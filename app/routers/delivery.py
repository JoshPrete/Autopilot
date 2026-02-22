from fastapi import APIRouter, Depends, Query
from sqlalchemy import text

from app.dependencies import get_validated_site
from app.schemas import SendRequest
from config.database import engine
from delivery.sender import send_to_role

router = APIRouter(prefix="/api/sites/{site_id}/delivery", tags=["delivery"])


@router.post("/send")
def send_message(body: SendRequest, site: dict = Depends(get_validated_site)):
    results = send_to_role(
        site_id=site["site_id"],
        role=body.role,
        message_body=body.message_body,
    )
    return {"deliveries": results}


@router.get("/log")
def get_delivery_log(
    site: dict = Depends(get_validated_site),
    limit: int = Query(default=50, ge=1, le=200),
):
    with engine.connect() as conn:
        rows = (
            conn.execute(
                text(
                    "SELECT * FROM recommendations "
                    "WHERE site_id = :site_id "
                    "ORDER BY action_timing DESC LIMIT :limit"
                ),
                {"site_id": site["site_id"], "limit": limit},
            )
            .mappings()
            .all()
        )
    return [dict(r) for r in rows]
