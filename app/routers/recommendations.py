from datetime import date

from fastapi import APIRouter, Depends, HTTPException

from app.dependencies import get_validated_site
from app.schemas import QueueSignalsRequest
from data.storage import get_prediction
from models.recommendations import generate_realtime_recommendations
from models.workload import QueueSignals, evaluate_transition_signals

router = APIRouter(prefix="/api/sites/{site_id}/recommendations", tags=["recommendations"])


@router.get("/today")
def get_today_recommendations(site: dict = Depends(get_validated_site)):
    prediction = get_prediction(site["site_id"], date.today())
    if not prediction:
        raise HTTPException(status_code=404, detail="No prediction for today")
    return prediction


@router.post("/evaluate")
def evaluate_signals(body: QueueSignalsRequest, site: dict = Depends(get_validated_site)):
    signals = QueueSignals(
        orders_per_5min=body.orders_per_5min,
        workload_units_per_15min=body.workload_units_per_15min,
        staff_on_floor=body.staff_on_floor,
        items_in_progress=body.items_in_progress,
        items_completed=body.items_completed,
        milk_drinks_queued=body.milk_drinks_queued,
        orders_waiting=body.orders_waiting,
        baseline_workload=body.baseline_workload,
        minutes_below_baseline=body.minutes_below_baseline,
        bar2_open=body.bar2_open,
        delivery_stacking=body.delivery_stacking,
        milk_queue_high=body.milk_queue_high,
    )
    transitions = evaluate_transition_signals(signals)
    recommendations = generate_realtime_recommendations(
        site_id=site["site_id"],
        prediction_id=body.prediction_id,
        triggered_transitions=transitions,
        staff_names=body.staff_names,
    )
    return {
        "transitions": transitions,
        "recommendations": recommendations,
    }
