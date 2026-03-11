from analysis.sale_understanding import (
    build_sale_profile_payload,
    classify_recipe_coverage,
    infer_sale_profile,
)


def test_infer_sale_profile_extracts_family_and_variant():
    profile = infer_sale_profile("12oz Iced Latte Takeaway")

    assert profile["family"] == "latte"
    assert profile["size_label"] == "12oz"
    assert profile["serve_temperature"] == "iced"
    assert profile["service_mode"] == "takeaway"
    assert profile["variant_key"] == "12oz_iced_takeaway"


def test_build_sale_profile_payload_is_safe_for_recipe_storage():
    payload = build_sale_profile_payload("Flat White")

    assert payload["family"] == "flat_white"
    assert payload["serve_temperature"] == "hot"


def test_classify_recipe_coverage_detects_family_only_match():
    coverage = classify_recipe_coverage(
        "12oz latte",
        operator_rules=[
            {
                "rule_type": "recipe_definition",
                "payload": {
                    "trigger_item_name": "latte",
                    "sale_profile": build_sale_profile_payload("latte"),
                },
            }
        ],
        usage_rules=[],
    )

    assert coverage["status"] == "family_only"
    assert coverage["matched_trigger"] == "latte"


def test_classify_recipe_coverage_detects_variant_match():
    coverage = classify_recipe_coverage(
        "12oz latte",
        operator_rules=[
            {
                "rule_type": "recipe_definition",
                "payload": {
                    "trigger_item_name": "12oz latte",
                    "sale_profile": build_sale_profile_payload("12oz latte"),
                },
            }
        ],
        usage_rules=[],
    )

    assert coverage["status"] in {"exact_match", "variant_match"}
