from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger("voice.catalog")

WORD_RE = re.compile(r"[a-z0-9]+")

SIZE_SYNONYMS = {
    "small": {"small", "8oz", "8", "short"},
    "medium": {"medium", "12oz", "12", "tall"},
    "large": {"large", "16oz", "16", "grande"},
}

MILK_SYNONYMS = {
    "whole milk": {"whole", "full cream"},
    "skim milk": {"skim", "skinny", "low fat"},
    "oat milk": {"oat"},
    "almond milk": {"almond"},
    "soy milk": {"soy"},
}

MODIFIER_SYNONYMS = {
    "extra shot": {"extra shot", "double shot"},
    "decaf": {"decaf"},
    "no syrup": {"no syrup", "without syrup"},
}


@dataclass
class CatalogModifier:
    id: str
    name: str
    list_id: Optional[str] = None


@dataclass
class CatalogVariation:
    id: str
    name: str


@dataclass
class CatalogItem:
    id: str
    name: str
    variations: List[CatalogVariation] = field(default_factory=list)


@dataclass
class CatalogIndex:
    items: List[CatalogItem]
    modifiers: List[CatalogModifier]

    @classmethod
    def from_square_catalog(cls, catalog: dict) -> "CatalogIndex":
        items: List[CatalogItem] = []
        modifiers: List[CatalogModifier] = []

        for item in catalog.get("items", {}).values():
            variations = []
            for variation in item.get("variations", []):
                if not variation.get("id"):
                    continue
                variations.append(
                    CatalogVariation(
                        id=variation.get("id"),
                        name=variation.get("name") or "",
                    )
                )
            items.append(
                CatalogItem(
                    id=item.get("catalog_item_id"),
                    name=item.get("item_name", ""),
                    variations=variations,
                )
            )

        for mod in catalog.get("modifiers", {}).values():
            modifiers.append(
                CatalogModifier(
                    id=mod.get("catalog_modifier_id"),
                    name=mod.get("modifier_name", ""),
                    list_id=mod.get("list_id"),
                )
            )

        return cls(items=items, modifiers=modifiers)

    @classmethod
    def fallback(cls) -> "CatalogIndex":
        items = [
            CatalogItem(
                id="fallback-latte",
                name="Latte",
                variations=[
                    CatalogVariation(id="fallback-8oz", name="8oz"),
                    CatalogVariation(id="fallback-12oz", name="12oz"),
                    CatalogVariation(id="fallback-16oz", name="16oz"),
                ],
            ),
            CatalogItem(id="fallback-capp", name="Cappuccino"),
            CatalogItem(id="fallback-espresso", name="Espresso"),
            CatalogItem(id="fallback-flat", name="Flat White"),
        ]
        modifiers = [
            CatalogModifier(id="fallback-oat", name="Oat Milk"),
            CatalogModifier(id="fallback-almond", name="Almond Milk"),
            CatalogModifier(id="fallback-whole", name="Whole Milk"),
            CatalogModifier(id="fallback-shot", name="Extra Shot"),
            CatalogModifier(id="fallback-decaf", name="Decaf"),
            CatalogModifier(id="fallback-nosyrup", name="No Syrup"),
        ]
        return cls(items=items, modifiers=modifiers)

    def find_item_matches(self, phrase: str, limit: int = 3) -> List[Tuple[CatalogItem, float]]:
        phrase_tokens = set(tokenize(phrase))
        scored = []
        for item in self.items:
            item_tokens = set(tokenize(item.name))
            if not item_tokens:
                continue
            overlap = len(phrase_tokens & item_tokens)
            ratio = SequenceMatcher(None, item.name.lower(), phrase.lower()).ratio()
            score = 0.6 * ratio + 0.4 * (overlap / max(len(item_tokens), 1))
            scored.append((item, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def match_variation(self, item: CatalogItem, phrase: str) -> Optional[CatalogVariation]:
        phrase_tokens = set(tokenize(phrase))
        for canonical, synonyms in SIZE_SYNONYMS.items():
            if phrase_tokens & synonyms:
                for variation in item.variations:
                    if canonical in variation.name.lower() or any(s in variation.name.lower() for s in synonyms):
                        return variation
        best = None
        best_score = 0.0
        for variation in item.variations:
            score = SequenceMatcher(None, variation.name.lower(), phrase.lower()).ratio()
            if score > best_score:
                best = variation
                best_score = score
        return best

    def match_modifiers(self, phrase: str) -> List[CatalogModifier]:
        phrase_norm = phrase.lower()
        matched: List[CatalogModifier] = []

        for canonical, synonyms in {**MILK_SYNONYMS, **MODIFIER_SYNONYMS}.items():
            if any(s in phrase_norm for s in synonyms):
                for mod in self.modifiers:
                    if canonical in mod.name.lower():
                        matched.append(mod)
                        break

        # Direct name match for any modifier tokens
        phrase_tokens = set(tokenize(phrase))
        for mod in self.modifiers:
            mod_tokens = set(tokenize(mod.name))
            if mod_tokens and mod_tokens.issubset(phrase_tokens):
                if mod not in matched:
                    matched.append(mod)

        return matched


def tokenize(text: str) -> List[str]:
    return WORD_RE.findall(text.lower())
