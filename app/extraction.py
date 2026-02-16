"""
Clubhouse Autopilot - Document Extraction Engine

Extracts structured data from uploaded documents (invoices, receipts, CSVs)
using Claude Vision. Updates item_costs and special_events tables.
"""

import base64
import csv
import io
import json
import logging
from pathlib import Path

import anthropic

from config.settings import settings
from data.storage import (
    get_document,
    has_real_cogs,
    store_special_event,
    update_document_extraction,
    upsert_item_cost,
)

logger = logging.getLogger("autopilot.extraction")

EXTRACTION_MODEL = "claude-sonnet-4-5-20250929"

EXTRACTION_SYSTEM_PROMPT = """\
You are a document extraction assistant for a specialty coffee cafe called Clubhouse.
Your job is to extract structured data from uploaded documents — invoices, receipts,
supplier price lists, or notes about closures/events.

Analyse the document and return a JSON object with this structure:
{
  "document_type": "invoice" | "receipt" | "csv_pricelist" | "closure_note" | "other",
  "supplier": "supplier name or null",
  "date": "YYYY-MM-DD or null",
  "summary": "One-line human-readable summary of this document",
  "is_cogs_document": true/false,
  "items": [
    {
      "score_key_guess": "lowercase_item_name (e.g. 'oat_milk', 'coffee_beans_1kg', 'cup_8oz')",
      "item_name": "Human-readable name",
      "category": "milk" | "coffee" | "cups" | "food" | "syrup" | "other",
      "unit_cost_cents": 150,
      "unit_description": "per litre" | "per kg" | "per unit" etc,
      "quantity": 10,
      "line_total_cents": 1500
    }
  ],
  "events": [
    {
      "event_name": "Early closure",
      "event_date": "YYYY-MM-DD",
      "event_type": "closure" | "holiday" | "market" | "festival",
      "impact": 0.5
    }
  ]
}

Rules:
- For coffee items, use score keys like: espresso_coffee, batch_brew_coffee, coffee_beans_1kg
- For milk, use: full_cream_milk, oat_milk, almond_milk, soy_milk, coconut_milk
- For cups: cup_8oz, cup_12oz, cup_16oz, lid_standard
- Convert all prices to cents (AUD)
- If the document mentions closures or events, include them in the events array
- If you can't extract items, return an empty items array
- Always return valid JSON, nothing else
"""


def build_extraction_content_blocks(doc: dict) -> list[dict]:
    """Convert a stored document into Anthropic API content blocks."""
    storage_path = doc["storage_path"]
    mime_type = doc["mime_type"]
    blocks = []

    if mime_type in ("image/jpeg", "image/png"):
        file_bytes = Path(storage_path).read_bytes()
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        blocks.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime_type,
                "data": b64,
            },
        })
    elif mime_type == "application/pdf":
        file_bytes = Path(storage_path).read_bytes()
        b64 = base64.standard_b64encode(file_bytes).decode("utf-8")
        blocks.append({
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": b64,
            },
        })
    elif mime_type == "text/csv":
        csv_text = Path(storage_path).read_text(errors="replace")
        # Truncate very large CSVs
        lines = csv_text.splitlines()
        if len(lines) > 200:
            csv_text = "\n".join(lines[:200]) + f"\n... ({len(lines) - 200} more rows)"
        blocks.append({
            "type": "text",
            "text": f"CSV file contents:\n```\n{csv_text}\n```",
        })

    blocks.append({
        "type": "text",
        "text": f"Filename: {doc['filename']}\nPlease extract structured data from this document.",
    })

    return blocks


def extract_document(document_id: str, site_id: str) -> dict:
    """
    Run extraction on a document using Claude.
    Returns the extracted data dict and updates the document record.
    """
    doc = get_document(document_id)
    if not doc:
        raise ValueError(f"Document {document_id} not found")

    if str(doc["site_id"]) != site_id:
        raise ValueError("Document does not belong to this site")

    content_blocks = build_extraction_content_blocks(doc)

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    response = client.messages.create(
        model=EXTRACTION_MODEL,
        max_tokens=2000,
        system=EXTRACTION_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content_blocks}],
    )

    raw_text = response.content[0].text.strip()

    # Parse JSON from response (handle markdown code blocks)
    if raw_text.startswith("```"):
        raw_text = raw_text.split("\n", 1)[1]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3]
        raw_text = raw_text.strip()

    try:
        extracted = json.loads(raw_text)
    except json.JSONDecodeError:
        logger.error("Failed to parse extraction JSON: %s", raw_text[:500])
        extracted = {
            "document_type": "other",
            "summary": "Could not extract structured data from this document",
            "items": [],
            "events": [],
        }

    # Apply COGS updates
    items_updated = []
    if extracted.get("is_cogs_document") and extracted.get("items"):
        items_updated = _apply_cogs_updates(site_id, extracted["items"])

    # Apply event updates
    if extracted.get("events"):
        _apply_event_updates(site_id, extracted["events"])

    # Update document record
    update_document_extraction(
        document_id=document_id,
        document_type=extracted.get("document_type", "other"),
        extracted_data=extracted,
        extraction_summary=extracted.get("summary", ""),
        items_updated=items_updated,
    )

    logger.info(
        "Extraction complete for %s: type=%s, %d items, %d events",
        document_id,
        extracted.get("document_type"),
        len(extracted.get("items", [])),
        len(extracted.get("events", [])),
    )

    return extracted


def _apply_cogs_updates(site_id: str, items: list[dict]) -> list[dict]:
    """Upsert item_costs from extracted invoice items. Returns list of updated items."""
    updated = []
    for item in items:
        score_key = item.get("score_key_guess")
        cost_cents = item.get("unit_cost_cents")
        if not score_key or not cost_cents:
            continue

        try:
            upsert_item_cost(
                site_id=site_id,
                score_key=score_key,
                category=item.get("category", "other"),
                cost_cents=int(cost_cents),
                description=item.get("item_name"),
                source="document",
            )
            updated.append({
                "score_key": score_key,
                "cost_cents": int(cost_cents),
                "item_name": item.get("item_name"),
            })
        except Exception as e:
            logger.error("Failed to upsert item cost %s: %s", score_key, e)

    return updated


def _apply_event_updates(site_id: str, events: list[dict]) -> None:
    """Insert special_events from extraction."""
    for event in events:
        name = event.get("event_name")
        event_date = event.get("event_date")
        if not name or not event_date:
            continue

        try:
            from datetime import date as date_type
            if isinstance(event_date, str):
                event_date = date_type.fromisoformat(event_date)

            store_special_event(
                site_id=site_id,
                name=name,
                event_date=event_date,
                event_type=event.get("event_type", "one_time"),
                impact=event.get("impact"),
            )
        except Exception as e:
            logger.error("Failed to store event %s: %s", name, e)
