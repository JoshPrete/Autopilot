from fastapi import APIRouter, Depends

from app.dependencies import get_validated_site

router = APIRouter(prefix="/api/sites/{site_id}", tags=["sites"])


@router.get("/status")
def site_status(site: dict = Depends(get_validated_site)):
    return site
