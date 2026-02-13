from __future__ import annotations

import logging
import re
from typing import List

from voice_order.catalog import CatalogIndex
from voice_order.schemas import ProposedLineItem, ProposedModifier

logger = logging.getLogger("voice.nlp")

SPLIT_RE = re.compile(r"\band\b|,", re.IGNORECASE)

NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}


def extract_quantity(phrase: str) -> int:
    tokens = phrase.lower().split()
    for token in tokens:
        if token.isdigit():
            return int(token)
        if token in NUMBER_WORDS:
            return NUMBER_WORDS[token]
    return 1


def parse_transcript(transcript: str, catalog: CatalogIndex) -> List[ProposedLineItem]:
    phrases = [p.strip() for p in SPLIT_RE.split(transcript) if p.strip()]
    items: List[ProposedLineItem] = []

    for phrase in phrases:
        matches = catalog.find_item_matches(phrase, limit=1)
        if not matches:
            items.append(
                ProposedLineItem(
                    name=phrase.title(),
                    quantity=extract_quantity(phrase),
                    confidence=0.2,
                    notes="No catalog match",
                )
            )
            continue

        item, score = matches[0]
        variation = catalog.match_variation(item, phrase)
        modifiers = catalog.match_modifiers(phrase)

        proposed_mods = [
            ProposedModifier(name=mod.name, catalog_object_id=mod.id, confidence=0.7)
            for mod in modifiers
        ]

        line_item = ProposedLineItem(
            name=item.name,
            quantity=extract_quantity(phrase),
            catalog_object_id=item.id,
            variation_id=variation.id if variation else None,
            modifiers=proposed_mods,
            confidence=score,
        )
        items.append(line_item)

    return items
