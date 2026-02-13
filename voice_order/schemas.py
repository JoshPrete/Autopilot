from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TranscriptRequest(BaseModel):
    transcript: str = Field(..., min_length=1)


class ProposedModifier(BaseModel):
    name: str
    catalog_object_id: Optional[str] = None
    confidence: float = 0.5


class ProposedLineItem(BaseModel):
    name: str
    quantity: int = 1
    catalog_object_id: Optional[str] = None
    variation_id: Optional[str] = None
    base_price_money: Optional[dict] = None
    modifiers: List[ProposedModifier] = Field(default_factory=list)
    confidence: float = 0.5
    notes: Optional[str] = None


class ParseResponse(BaseModel):
    transcript: str
    items: List[ProposedLineItem]
    warnings: List[str] = Field(default_factory=list)


class LineItemInput(BaseModel):
    quantity: str
    catalog_object_id: Optional[str] = None
    name: Optional[str] = None
    base_price_money: Optional[dict] = None
    modifiers: Optional[list] = None


class CreateOrderRequest(BaseModel):
    location_id: Optional[str] = None
    line_items: List[LineItemInput]
    pickup_at: Optional[str] = None
    recipient_name: Optional[str] = None


class CreateOrderResponse(BaseModel):
    order_id: str
    state: str
    total_money: Optional[dict] = None


class CreateCheckoutRequest(BaseModel):
    order_id: str
    device_id: Optional[str] = None
    amount_money: Optional[dict] = None
    note: Optional[str] = None


class CreateCheckoutResponse(BaseModel):
    checkout_id: str
    status: str
    order_id: Optional[str] = None


class MockPaymentRequest(BaseModel):
    order_id: str
    status: str = "COMPLETED"
    note: Optional[str] = None


class MockPaymentResponse(BaseModel):
    order_id: str
    status: str


class SandboxPaymentRequest(BaseModel):
    order_id: str
    amount_money: dict
    source_id: Optional[str] = None
    note: Optional[str] = None


class SandboxPaymentResponse(BaseModel):
    payment_id: str
    status: str
    order_id: Optional[str] = None
