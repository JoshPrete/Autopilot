from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from square.utils.webhooks_helper import verify_signature

from config.settings import settings
from voice_order.catalog import CatalogIndex
from voice_order.nlp import parse_transcript
from voice_order.schemas import (
    CreateCheckoutRequest,
    CreateCheckoutResponse,
    CreateOrderRequest,
    CreateOrderResponse,
    MockPaymentRequest,
    MockPaymentResponse,
    ParseResponse,
    SandboxPaymentRequest,
    SandboxPaymentResponse,
    TranscriptRequest,
)
from voice_order.square_service import SquareVoiceService, SquareVoiceServiceError

logger = logging.getLogger("voice.app")

app = FastAPI(title="Voice Order Sidecar", version="0.1.0")

catalog_index: Optional[CatalogIndex] = None
square_service: Optional[SquareVoiceService] = None
recent_webhooks: list[dict] = []
order_status: dict[str, str] = {}


@app.on_event("startup")
def startup() -> None:
    global catalog_index, square_service

    try:
        square_service = SquareVoiceService()
        catalog = square_service.fetch_catalog()
        catalog_index = CatalogIndex.from_square_catalog(catalog)
        logger.info("Loaded catalog with %d items", len(catalog_index.items))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falling back to sample catalog: %s", exc)
        catalog_index = CatalogIndex.fallback()
        square_service = None

    static_dir = Path(__file__).with_name("static")
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/", response_class=HTMLResponse)
def index() -> HTMLResponse:
    static_dir = Path(__file__).with_name("static")
    index_path = static_dir / "index.html"
    if not index_path.exists():
        return HTMLResponse("<h1>Voice Order POC</h1><p>Static UI not found.</p>")
    return HTMLResponse(index_path.read_text())


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "catalog_items": len(catalog_index.items) if catalog_index else 0,
        "square_env": settings.SQUARE_ENVIRONMENT,
    }


@app.get("/voice/catalog")
def voice_catalog() -> dict:
    if not catalog_index:
        raise HTTPException(status_code=500, detail="Catalog not initialized")

    return {
        "items": [
            {
                "id": item.id,
                "name": item.name,
                "variations": [{"id": v.id, "name": v.name} for v in item.variations],
            }
            for item in catalog_index.items
        ],
        "modifiers": [
            {"id": mod.id, "name": mod.name, "list_id": mod.list_id}
            for mod in catalog_index.modifiers
        ],
    }


@app.post("/voice/parse", response_model=ParseResponse)
def voice_parse(payload: TranscriptRequest) -> ParseResponse:
    if not catalog_index:
        raise HTTPException(status_code=500, detail="Catalog not initialized")

    items = parse_transcript(payload.transcript, catalog_index)
    warnings = []
    if all(item.confidence < 0.4 for item in items):
        warnings.append("Low confidence matches - review before confirming")

    return ParseResponse(transcript=payload.transcript, items=items, warnings=warnings)


@app.post("/voice/confirm", response_model=CreateOrderResponse)
def voice_confirm(payload: CreateOrderRequest) -> CreateOrderResponse:
    if not square_service:
        raise HTTPException(status_code=500, detail="Square service not initialized")

    line_items = []
    for item in payload.line_items:
        entry = item.model_dump(exclude_none=True)
        if "catalog_object_id" not in entry and "name" not in entry:
            raise HTTPException(status_code=400, detail="Line item missing name or catalog_object_id")
        line_items.append(entry)

    try:
        order = square_service.create_order(
            line_items=line_items,
            pickup_at=payload.pickup_at,
            recipient_name=payload.recipient_name,
            location_id=payload.location_id,
        )
    except SquareVoiceServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return CreateOrderResponse(
        order_id=order.get("id", ""),
        state=order.get("state", ""),
        total_money=order.get("total_money"),
    )


@app.post("/voice/checkout", response_model=CreateCheckoutResponse)
def voice_checkout(payload: CreateCheckoutRequest) -> CreateCheckoutResponse:
    if not square_service:
        raise HTTPException(status_code=500, detail="Square service not initialized")

    device_id = payload.device_id or getattr(settings, "SQUARE_TERMINAL_DEVICE_ID", "")
    if not device_id:
        raise HTTPException(status_code=400, detail="Terminal device_id required")

    try:
        checkout = square_service.create_terminal_checkout(
            order_id=payload.order_id,
            device_id=device_id,
            amount_money=payload.amount_money,
            note=payload.note,
        )
    except SquareVoiceServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return CreateCheckoutResponse(
        checkout_id=checkout.get("id", ""),
        status=checkout.get("status", ""),
        order_id=checkout.get("order_id"),
    )


@app.post("/voice/mock-payment", response_model=MockPaymentResponse)
def voice_mock_payment(payload: MockPaymentRequest) -> MockPaymentResponse:
    order_status[payload.order_id] = payload.status
    recent_webhooks.append(
        {
            "event_id": f"mock-{payload.order_id}",
            "type": "terminal.checkout.updated",
            "created_at": "mock",
        }
    )
    del recent_webhooks[:-20]
    return MockPaymentResponse(order_id=payload.order_id, status=payload.status)


@app.post("/voice/pay-sandbox", response_model=SandboxPaymentResponse)
def voice_pay_sandbox(payload: SandboxPaymentRequest) -> SandboxPaymentResponse:
    if not square_service:
        raise HTTPException(status_code=500, detail="Square service not initialized")
    if settings.SQUARE_ENVIRONMENT.lower() != "sandbox":
        raise HTTPException(status_code=400, detail="Sandbox payments require SQUARE_ENVIRONMENT=sandbox")

    source_id = payload.source_id or settings.SQUARE_SANDBOX_TEST_SOURCE_ID
    try:
        payment = square_service.create_payment(
            order_id=payload.order_id,
            amount_money=payload.amount_money,
            source_id=source_id,
            note=payload.note,
        )
    except SquareVoiceServiceError as exc:
        raise HTTPException(status_code=502, detail=str(exc))

    return SandboxPaymentResponse(
        payment_id=payment.get("id", ""),
        status=payment.get("status", ""),
        order_id=payment.get("order_id"),
    )


@app.post("/webhooks/square")
async def square_webhook(request: Request) -> JSONResponse:
    body = await request.body()
    signature = request.headers.get("x-square-hmacsha256-signature", "")
    notification_url = settings.SQUARE_WEBHOOK_NOTIFICATION_URL
    signature_key = settings.SQUARE_WEBHOOK_SIGNATURE_KEY

    if signature_key and notification_url:
        if not verify_signature(body.decode("utf-8"), signature, signature_key, notification_url):
            return JSONResponse(status_code=401, content={"status": "invalid signature"})
    else:
        logger.warning("Webhook signature verification disabled (missing env vars)")

    payload = await request.json()
    recent_webhooks.append(
        {
            "event_id": payload.get("event_id"),
            "type": payload.get("type"),
            "created_at": payload.get("created_at"),
        }
    )
    del recent_webhooks[:-20]
    return JSONResponse(content={"status": "ok"})


@app.get("/webhooks/recent")
def recent_webhook_events() -> dict:
    return {"events": list(recent_webhooks)}
