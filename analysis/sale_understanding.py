"""
Helpers for understanding what a sale item represents operationally.

This lets the system reason about product families and variants instead
of matching recipes only by exact item string.
"""

from __future__ import annotations

import re

_SIZE_PATTERNS = [
    (re.compile(r"\b16\s*oz\b", re.IGNORECASE), {"size_label": "16oz", "size_oz": 16}),
    (re.compile(r"\b12\s*oz\b", re.IGNORECASE), {"size_label": "12oz", "size_oz": 12}),
    (re.compile(r"\b8\s*oz\b", re.IGNORECASE), {"size_label": "8oz", "size_oz": 8}),
    (re.compile(r"\blarge\b", re.IGNORECASE), {"size_label": "large", "size_oz": None}),
    (re.compile(r"\bregular\b", re.IGNORECASE), {"size_label": "regular", "size_oz": None}),
    (re.compile(r"\bsmall\b", re.IGNORECASE), {"size_label": "small", "size_oz": None}),
]

_FAMILY_PATTERNS = [
    ("flat_white", re.compile(r"\bflat\s+white\b", re.IGNORECASE)),
    ("long_black", re.compile(r"\blong\s+black\b", re.IGNORECASE)),
    ("batch_brew", re.compile(r"\bbatch\s+brew\b", re.IGNORECASE)),
    ("cold_brew", re.compile(r"\bcold\s+brew\b", re.IGNORECASE)),
    ("cappuccino", re.compile(r"\bcappuccino\b", re.IGNORECASE)),
    ("croissant", re.compile(r"\bcroissant\b", re.IGNORECASE)),
    ("smoothie", re.compile(r"\bsmoothie\b", re.IGNORECASE)),
    ("espresso", re.compile(r"\bespresso\b", re.IGNORECASE)),
    ("matcha", re.compile(r"\bmatcha\b", re.IGNORECASE)),
    ("muffin", re.compile(r"\bmuffin\b", re.IGNORECASE)),
    ("cookie", re.compile(r"\bcookie\b", re.IGNORECASE)),
    ("latte", re.compile(r"\blatte\b", re.IGNORECASE)),
    ("mocha", re.compile(r"\bmocha\b", re.IGNORECASE)),
    ("wrap", re.compile(r"\bwrap\b", re.IGNORECASE)),
    ("chai", re.compile(r"\bchai\b", re.IGNORECASE)),
    ("juice", re.compile(r"\bjuice\b", re.IGNORECASE)),
]


def normalize_sale_label(raw: str | None) -> str:
    text = str(raw or "").strip().lower().replace("_", " ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def infer_sale_profile(item_name: str | None) -> dict:
    normalized = normalize_sale_label(item_name)
    family = None
    for family_key, pattern in _FAMILY_PATTERNS:
        if pattern.search(str(item_name or "")):
            family = family_key
            break

    size_label = None
    size_oz = None
    for pattern, payload in _SIZE_PATTERNS:
        if pattern.search(str(item_name or "")):
            size_label = payload["size_label"]
            size_oz = payload["size_oz"]
            break

    if re.search(r"\b(iced|ice|cold)\b", str(item_name or ""), re.IGNORECASE):
        serve_temperature = "iced"
    elif family in {
        "latte",
        "cappuccino",
        "flat_white",
        "mocha",
        "matcha",
        "chai",
        "long_black",
        "espresso",
        "batch_brew",
    }:
        serve_temperature = "hot"
    else:
        serve_temperature = None

    if re.search(r"\b(takeaway|take away|to go|togo)\b", str(item_name or ""), re.IGNORECASE):
        service_mode = "takeaway"
    elif re.search(r"\b(dine in|mug|ceramic|cup in)\b", str(item_name or ""), re.IGNORECASE):
        service_mode = "dine_in"
    else:
        service_mode = None

    variant_parts = []
    if size_label:
        variant_parts.append(size_label)
    if serve_temperature:
        variant_parts.append(serve_temperature)
    if service_mode:
        variant_parts.append(service_mode)

    return {
        "normalized_name": normalized,
        "family": family,
        "size_label": size_label,
        "size_oz": size_oz,
        "serve_temperature": serve_temperature,
        "service_mode": service_mode,
        "variant_key": "_".join(variant_parts) if variant_parts else None,
    }


def build_sale_profile_payload(item_name: str | None) -> dict:
    profile = infer_sale_profile(item_name)
    return {
        "family": profile.get("family"),
        "size_label": profile.get("size_label"),
        "size_oz": profile.get("size_oz"),
        "serve_temperature": profile.get("serve_temperature"),
        "service_mode": profile.get("service_mode"),
        "variant_key": profile.get("variant_key"),
    }


def classify_recipe_coverage(
    item_name: str | None,
    operator_rules: list[dict] | None = None,
    usage_rules: list[dict] | None = None,
) -> dict:
    sale_profile = infer_sale_profile(item_name)
    best = {
        "status": "uncovered",
        "matched_trigger": None,
        "match_source": None,
        "sale_profile": sale_profile,
    }
    rank = {"uncovered": 0, "family_only": 1, "variant_match": 2, "exact_match": 3}

    def maybe_update(status: str, trigger_name: str | None, source: str) -> None:
        if rank[status] <= rank[best["status"]]:
            return
        best.update(
            {
                "status": status,
                "matched_trigger": trigger_name,
                "match_source": source,
            }
        )

    candidates = []
    for rule in usage_rules or []:
        candidates.append(
            {
                "trigger_item_name": rule.get("trigger_item_name"),
                "sale_profile": build_sale_profile_payload(rule.get("trigger_item_name")),
                "source": "inventory_usage_rule",
            }
        )

    for rule in operator_rules or []:
        if rule.get("rule_type") != "recipe_definition":
            continue
        payload = rule.get("payload") or {}
        candidates.append(
            {
                "trigger_item_name": payload.get("trigger_item_name"),
                "sale_profile": payload.get("sale_profile")
                or build_sale_profile_payload(payload.get("trigger_item_name")),
                "source": "recipe_definition",
            }
        )

    for candidate in candidates:
        trigger_name = candidate.get("trigger_item_name")
        candidate_profile = candidate.get("sale_profile") or {}
        trigger_normalized = normalize_sale_label(trigger_name)
        if not trigger_normalized:
            continue

        if trigger_normalized == sale_profile["normalized_name"]:
            maybe_update("exact_match", trigger_name, candidate.get("source") or "unknown")
            continue

        if (
            sale_profile.get("family")
            and candidate_profile.get("family")
            and sale_profile["family"] == candidate_profile["family"]
        ):
            variant_attrs = (
                "size_label",
                "size_oz",
                "serve_temperature",
                "service_mode",
            )
            specific_values = sum(
                1 for attr in variant_attrs if candidate_profile.get(attr) not in (None, "")
            )
            missing_specificity = any(
                sale_profile.get(attr) not in (None, "")
                and candidate_profile.get(attr) in (None, "")
                for attr in variant_attrs
            )
            compatible = all(
                candidate_profile.get(attr) in (None, "", sale_profile.get(attr))
                for attr in variant_attrs
            )
            if compatible and specific_values > 0 and not missing_specificity:
                maybe_update("variant_match", trigger_name, candidate.get("source") or "unknown")
            elif compatible:
                maybe_update("family_only", trigger_name, candidate.get("source") or "unknown")

    return best
