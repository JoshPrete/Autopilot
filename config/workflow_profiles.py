"""
Clubhouse workflow profiles and role responsibilities.

This captures operator-defined role intent so analysis can reason about
where bottlenecks appear as staffing levels change.
"""

PRIORITIES = [
    "greet_serve_customers",
    "deliver_coffees_food",
    "pull_shots",
    "prep_cups",
]


WORKFLOW_ROLES = {
    4: [
        {
            "role": "P1",
            "zone": "front",
            "focus": "customer_service",
            "responsibilities": [
                "greet_and_take_orders",
                "direct_customer_flow",
            ],
        },
        {
            "role": "P2",
            "zone": "shots",
            "focus": "coffee",
            "responsibilities": [
                "pull_shots_and_prepare_bases",
                "monitor_shot_time_and_grind",
                "hourly_shot_weight_checks",
            ],
        },
        {
            "role": "P3",
            "zone": "back_finish",
            "focus": "coffee",
            "responsibilities": [
                "finish_drinks",
                "coordinate_bases_and_iced_prep",
                "steam_hygiene_and_wally_temp",
            ],
        },
        {
            "role": "P4",
            "zone": "delivery_support",
            "focus": "customer_service",
            "responsibilities": [
                "deliver_orders",
                "prep_ice_cups",
                "restock_consumables",
            ],
        },
    ],
    3: [
        {
            "role": "P1",
            "zone": "front_then_shots",
            "focus": "customer_service_then_coffee",
            "responsibilities": [
                "greet_and_take_orders",
                "move_to_shots_when_queue_clear",
            ],
        },
        {
            "role": "P2",
            "zone": "back",
            "focus": "coffee",
            "responsibilities": [
                "prepare_shots_then_finish_drinks",
                "use_wally_for_first_hot_drinks",
            ],
        },
        {
            "role": "P3",
            "zone": "delivery_prep",
            "focus": "customer_service_support",
            "responsibilities": [
                "prep_cups_from_order_listen",
                "deliver_finished_drinks",
            ],
        },
    ],
    2: [
        {
            "role": "P1",
            "zone": "front_then_shots",
            "focus": "customer_service_then_coffee",
            "responsibilities": [
                "greet_and_take_orders",
                "move_to_shots_when_queue_clear",
                "assist_delivery_when_possible",
            ],
        },
        {
            "role": "P2",
            "zone": "back",
            "focus": "coffee_then_delivery",
            "responsibilities": [
                "prepare_shots_and_wally_milk",
                "finish_drinks",
                "deliver_if_shot_or_milk_waiting",
            ],
        },
    ],
    1: [
        {
            "role": "P1",
            "zone": "solo",
            "focus": "sequenced_priority_execution",
            "responsibilities": [
                "set_customer_wait_expectations",
                "start_wally_for_primary_milk",
                "run_groupheads_continuously",
                "prep_specialty_bases_and_ice",
                "stage_next_jug_before_delivery",
            ],
        },
    ],
}


# Workload risk thresholds by staffing mode (workload units per person per 15 min).
WORKLOAD_PER_PERSON_THRESHOLDS = {
    1: {"green": 2.4, "yellow": 3.1},
    2: {"green": 2.8, "yellow": 3.4},
    3: {"green": 3.0, "yellow": 3.7},
    4: {"green": 3.2, "yellow": 3.9},
}


def minimum_viable_staff(workload_units: float) -> int:
    """Smallest N (1..4) where wu/N <= yellow_threshold[N]."""
    if workload_units <= 0:
        return 0
    for n in range(1, 5):
        if workload_units / n <= WORKLOAD_PER_PERSON_THRESHOLDS[n]["yellow"]:
            return n
    return 4
