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


class RecommendationFeedbackRequest(BaseModel):
    adopted: bool
    manager_name: str = "manager"
    helpfulness_rating: Optional[int] = Field(default=None, ge=1, le=5)
    rush_timing_rating: Optional[int] = Field(default=None, ge=1, le=5)
    notes: Optional[str] = None
    log_date: Optional[date] = None


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


# --- Inventory ---

class InventoryItemUpsertRequest(BaseModel):
    item_name: str
    score_key: Optional[str] = None
    unit: str = "units"
    reorder_point: float = Field(default=0, ge=0)
    par_level: Optional[float] = Field(default=None, ge=0)
    lead_time_days: int = Field(default=2, ge=0, le=30)
    active: bool = True
    metadata: Optional[dict] = None


class InventoryCountRequest(BaseModel):
    quantity_on_hand: float = Field(..., ge=0)
    counted_at: Optional[datetime] = None
    source: str = "manual"
    notes: Optional[str] = None


class InventoryUsageRuleUpsertRequest(BaseModel):
    inventory_item_id: str
    trigger_item_name: str
    units_per_sale: float = Field(..., ge=0)
    required_modifier_terms: Optional[str] = None
    excluded_modifier_terms: Optional[str] = None
    priority: int = Field(default=100, ge=1, le=1000)
    active: bool = True


# --- Tomorrow Plan ---

class TomorrowPlanParams(BaseModel):
    staff_names: Optional[dict] = None


# --- Chat ---

class ChatMessage(BaseModel):
    role: str  # "user" or "assistant"
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    document_ids: list[str] = []
