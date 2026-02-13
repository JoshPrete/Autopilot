from fastapi import HTTPException, Path

from data.storage import get_site


def get_validated_site(site_id: str = Path(..., description="Site identifier")) -> dict:
    """Validate that site_id exists and return site dict, or raise 404."""
    site = get_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail=f"Site '{site_id}' not found")
    return site
