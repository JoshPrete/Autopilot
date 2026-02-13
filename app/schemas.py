from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


# --- Workload ---

class ModifierInput(BaseModel):
    modifier_name: str
    modifier_type: str = "UNKNOWN"


class LineItemInput(BaseModel):
    item_name: str
    quantity: int = 1
    modifiers: list[ModifierInput] = []


class OrderInput(BaseModel):
    order_id: str
    site_id: str
    created_at: Optional[str] = None
    line_items: list[LineItemInput]


class WorkloadRequest(BaseModel):
    order: OrderInput


# --- Predictions ---

class GeneratePredictionRequest(BaseModel):
    target_date: Optional[date] = None
    staff_scheduled: Optional[int] = None
    save: bool = True


# --- Recommendations ---

class QueueSignalsRequest(BaseModel):
    prediction_id: str
    orders_per_5min: int = 0
    workload_units_per_15min: float = 0.0
    staff_on_floor: int = 2
    items_in_progress: Optional[int] = None
    items_completed: Optional[int] = None
    milk_drinks_queued: int = 0
    orders_waiting: int = 0
    baseline_workload: float = 0.0
    minutes_below_baseline: int = 0
    bar2_open: bool = False
    delivery_stacking: bool = False
    milk_queue_high: bool = False
    staff_names: Optional[dict] = None


# --- Pipeline ---

class IngestRequest(BaseModel):
    run_date: Optional[date] = None
    dry_run: bool = False


class PredictRequest(BaseModel):
    run_date: Optional[date] = None
    staff_scheduled: Optional[int] = None
    staff_names: Optional[dict] = None
    dry_run: bool = False


# --- Delivery ---

class SendRequest(BaseModel):
    role: str
    message_body: str


# --- Analysis ---

class AccuracyParams(BaseModel):
    days_back: int = 7
    reference_date: Optional[date] = None


class AdoptionParams(BaseModel):
    start_date: date
    end_date: date


class WeeklyReviewParams(BaseModel):
    week_end: Optional[date] = None


# --- Tomorrow Plan ---

class TomorrowPlanParams(BaseModel):
    staff_names: Optional[dict] = None


# --- Chat ---

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
