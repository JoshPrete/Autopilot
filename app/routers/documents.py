"""
Clubhouse Autopilot - Document Upload Endpoint
POST /api/sites/{site_id}/documents/upload → accepts file, returns document_id
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, UploadFile

from app.dependencies import get_validated_site
from config.settings import settings
from data.storage import store_document

logger = logging.getLogger("autopilot.documents")

router = APIRouter(prefix="/api/sites/{site_id}/documents", tags=["documents"])

ALLOWED_MIME_TYPES = {
    "image/jpeg",
    "image/png",
    "application/pdf",
    "text/csv",
}

MAX_SIZE_BYTES = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024


@router.post("/upload")
async def upload_document(
    file: UploadFile,
    site: dict = Depends(get_validated_site),
):
    """
    Upload a document (invoice, receipt, CSV price list).
    Returns document_id for use with chat messages.
    """
    site_id = str(site["site_id"])

    # Validate mime type
    if file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"File type '{file.content_type}' not allowed. "
                   f"Accepted: JPEG, PNG, PDF, CSV",
        )

    # Read file content
    content = await file.read()

    # Validate size
    if len(content) > MAX_SIZE_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large ({len(content) / 1024 / 1024:.1f}MB). "
                   f"Max: {settings.MAX_UPLOAD_SIZE_MB}MB",
        )

    # Create storage directory
    upload_dir = Path(settings.UPLOAD_DIR) / site_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Generate safe filename
    import uuid
    ext = Path(file.filename or "file").suffix
    safe_name = f"{uuid.uuid4().hex}{ext}"
    file_path = upload_dir / safe_name

    # Write file
    file_path.write_bytes(content)

    # Store metadata
    document_id = store_document(
        site_id=site_id,
        filename=file.filename or "unnamed",
        mime_type=file.content_type,
        file_size_bytes=len(content),
        storage_path=str(file_path),
    )

    logger.info("Uploaded document %s: %s (%d bytes)", document_id, file.filename, len(content))

    return {
        "document_id": document_id,
        "filename": file.filename,
        "mime_type": file.content_type,
        "size_bytes": len(content),
    }
