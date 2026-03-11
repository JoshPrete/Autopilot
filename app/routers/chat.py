"""
Clubhouse Autopilot - Chat API Endpoints
POST /api/sites/{site_id}/chat/message → SSE streaming response
GET  /api/sites/{site_id}/chat/agenda  → curiosity agenda + opener question
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from app.chat import stream_chat_response
from app.dependencies import get_validated_site
from app.limiter import limiter
from app.schemas import ChatRequest

router = APIRouter(prefix="/api/sites/{site_id}/chat", tags=["chat"])


@router.get("/agenda")
def get_chat_agenda(site: dict = Depends(get_validated_site)) -> dict:
    """
    Return the current curiosity agenda for this site.

    Used by the chat UI to pre-load a proactive opener question and
    suggestion chips without requiring the user to send the first message.
    """
    from analysis.curiosity import build_curiosity_agenda

    site_id = site["site_id"]
    agenda = build_curiosity_agenda(site_id, limit=6)

    opener: str | None = None
    if agenda:
        top = agenda[0]
        opener = top.get("question") or top.get("title")

    return {
        "site_id": site_id,
        "agenda": agenda,
        "opener": opener,
    }


@router.post("/message")
@limiter.limit("30/minute")
async def chat_message(
    request: Request,
    body: ChatRequest,
    site: dict = Depends(get_validated_site),
):
    """
    Send a chat message and receive a streaming response.

    Accepts conversation history as a list of {role, content} messages.
    Optionally includes document_ids for uploaded files to extract.
    Returns Server-Sent Events with partial text chunks.
    """
    messages = [{"role": m.role, "content": m.content} for m in body.messages]

    return StreamingResponse(
        stream_chat_response(
            site_id=site["site_id"],
            site_name=site.get("name", "Clubhouse"),
            messages=messages,
            document_ids=body.document_ids,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
