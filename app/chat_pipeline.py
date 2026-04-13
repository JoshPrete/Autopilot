from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date


@dataclass
class ChatResponseEnvelope:
    draft_answer: str
    final_answer: str
    warnings: list[str] = field(default_factory=list)
    follow_up_questions: list[str] = field(default_factory=list)
    follow_up_hint: str | None = None
    blocked: bool = False
    block_reason: str | None = None
    applied_rules: list[dict] = field(default_factory=list)

    def to_payload(self) -> dict:
        return asdict(self)


def format_local_date(raw_date: str | None) -> str:
    if not raw_date:
        return "unknown"
    try:
        return date.fromisoformat(str(raw_date)).strftime("%d/%m/%Y")
    except ValueError:
        return str(raw_date)


def source_label(source: str) -> str:
    labels = {
        "square_orders": "Square orders",
        "daily_profitability": "Daily profitability",
        "deputy_rosters": "Deputy rosters",
        "xero_cogs": "Xero COGS",
        "xero_financial_facts": "Xero financial facts",
        "data_quality_flags": "Data quality flags",
    }
    return labels.get(source, source)


def data_health_components(context: dict) -> dict[str, dict]:
    health = context.get("data_health") or {}
    components = health.get("components") or []
    return {
        str(component.get("source") or "").strip(): component
        for component in components
        if str(component.get("source") or "").strip()
    }


def keyword_match(question: str, keywords: list[str]) -> bool:
    q = (question or "").lower()
    return any(kw in q for kw in keywords)


def is_broad_learning_question(question: str) -> bool:
    return keyword_match(
        question,
        [
            "what next",
            "what should",
            "how can",
            "improve",
            "profit",
            "profitability",
            "efficiency",
            "workflow",
            "optimi",
            "missing",
            "learn",
            "understand",
            "why",
            "opportunit",
        ],
    )


def curiosity_item_is_capture_ready(item: dict) -> bool:
    gap_type = str(item.get("source_gap_type") or "").strip()
    agenda_type = str(item.get("agenda_type") or "").strip()
    return (
        gap_type in {"missing_recipe", "missing_recipe_variant", "missing_delivery_schedule"}
        or agenda_type == "consumption_mapping"
    )


def curiosity_item_score(question: str, item: dict) -> int:
    score = {"high": 300, "medium": 200, "low": 100}.get(
        str(item.get("priority") or "medium"),
        100,
    )
    agenda_type = str(item.get("agenda_type") or "").strip()
    gap_type = str(item.get("source_gap_type") or "").strip()

    if is_broad_learning_question(question):
        score += 120

    if keyword_match(question, ["inventory", "stock", "recipe", "ingredient", "cogs", "sale"]):
        if gap_type in {"missing_recipe", "missing_recipe_variant", "missing_delivery_schedule"}:
            score += 140
        if agenda_type == "consumption_mapping":
            score += 160

    if keyword_match(question, ["profit", "profitability", "margin", "labor", "efficiency"]):
        if agenda_type in {
            "knowledge_gap",
            "workflow_learning",
            "consumption_mapping",
            "labor_explanation",
        }:
            score += 90

    if keyword_match(question, ["xero", "expense", "supplier", "purchase", "invoice", "bill"]):
        if agenda_type == "purchase_explanation":
            score += 180

    if keyword_match(question, ["wage", "labor", "payroll", "why"]):
        if agenda_type == "labor_explanation":
            score += 160

    if keyword_match(question, ["why", "missing", "learn", "understand"]):
        score += 70

    if curiosity_item_is_capture_ready(item):
        score += 60

    return score


def recent_assistant_messages(messages: list[dict], limit: int = 4) -> list[str]:
    recent: list[str] = []
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = str(msg.get("content") or "").strip()
        if content:
            recent.append(content.lower())
        for question in msg.get("follow_up_questions") or []:
            question_text = str(question or "").strip()
            if question_text:
                recent.append(question_text.lower())
        if len(recent) >= limit:
            return recent[:limit]
    return recent


def was_curiosity_item_recently_asked(item: dict, messages: list[dict]) -> bool:
    recent_assistant = recent_assistant_messages(messages)
    if not recent_assistant:
        return False

    question = str(item.get("question") or "").strip().lower()
    title = str(item.get("title") or "").strip().lower()
    markers = [marker for marker in [question, title] if marker]
    if not markers:
        return False

    for content in recent_assistant:
        if any(marker in content for marker in markers):
            return True
    return False


def select_curiosity_item(question: str, context: dict, messages: list[dict]) -> dict | None:
    agenda = context.get("curiosity_agenda") or []
    if not agenda:
        return None

    scored = sorted(
        ((item, curiosity_item_score(question, item)) for item in agenda),
        key=lambda pair: pair[1],
        reverse=True,
    )

    for item, score in scored:
        if score < 320:
            return None
        if was_curiosity_item_recently_asked(item, messages):
            continue
        if curiosity_item_is_capture_ready(item):
            return item
        if is_broad_learning_question(question) and str(item.get("priority") or "") == "high":
            return item

    return None


def question_has_currentness(question: str) -> bool:
    return keyword_match(
        question,
        [
            "today",
            "current",
            "currently",
            "now",
            "right now",
            "latest",
            "recent",
            "this week",
            "this month",
            "yesterday",
            "tonight",
            "this morning",
            "this afternoon",
            "this evening",
        ],
    )


def required_data_sources(question: str) -> list[str]:
    required: list[str] = []

    if keyword_match(
        question,
        [
            "sales",
            "revenue",
            "trade",
            "orders",
            "items",
            "item mix",
            "drinks",
            "top seller",
            "toastie",
            "menu",
        ],
    ):
        required.append("square_orders")

    if keyword_match(
        question,
        [
            "labor",
            "labour",
            "wage",
            "wages",
            "staffing",
            "staff",
            "profit",
            "profitability",
            "margin",
            "p&l",
            "pnl",
            "efficiency",
            "rev/labor",
            "rev per labor",
        ],
    ):
        required.append("daily_profitability")

    if keyword_match(
        question,
        [
            "roster",
            "rosters",
            "shift",
            "shifts",
            "deputy",
            "working tomorrow",
            "working today",
            "who's on",
            "who is on",
            "who is working",
            "who's working",
        ],
    ):
        required.append("deputy_rosters")

    if keyword_match(
        question,
        [
            "xero",
            "expense",
            "expenses",
            "incoming",
            "outgoing",
            "payroll",
            "cash",
            "bank",
            "bill",
            "bills",
            "invoice",
            "invoices",
            "reconciled",
            "financial truth",
        ],
    ):
        required.append("xero_financial_facts")

    if keyword_match(question, ["cogs", "cost", "costs", "supplier cost"]):
        required.append("xero_cogs")

    ordered: list[str] = []
    for source in required:
        if source not in ordered:
            ordered.append(source)
    return ordered


def build_question_source_basis(question: str, context: dict) -> list[dict]:
    components = data_health_components(context)
    basis: list[dict] = []
    for source in required_data_sources(question):
        component = components.get(source)
        if not component:
            continue
        basis.append(
            {
                "source": source,
                "label": source_label(source),
                "status": str(component.get("status") or "unknown"),
                "latest_date": component.get("latest_date"),
                "age_days": component.get("age_days"),
            }
        )
    return basis


def curiosity_reply_hint(item: dict) -> str | None:
    gap_type = str(item.get("source_gap_type") or "").strip()
    title = str(item.get("title") or "")

    if gap_type in {"missing_recipe", "missing_recipe_variant"}:
        return "Reply in a recipe form like `12oz latte uses 1 cup, 1 lid, 20g beans, 280ml full cream milk`."
    if gap_type == "missing_delivery_schedule":
        return (
            "Reply in a schedule form like `Oat milk delivery is Monday, Wednesday, Friday` "
            "or `We place oat milk orders by 2pm Tuesday for Wednesday delivery`."
        )
    if str(item.get("agenda_type") or "") == "consumption_mapping" or "consume" in title.lower():
        return "Reply in a rule form like `12oz coffee uses 1 12oz cup and 1 90mm lid`."
    if str(item.get("agenda_type") or "") == "purchase_explanation":
        return (
            "Reply in plain language with what the expense is for and whether it is stock, packaging, "
            "maintenance, marketing, or overhead."
        )
    if str(item.get("agenda_type") or "") == "labor_explanation":
        return (
            "Reply in plain language with the cause, such as training, sick cover, public-holiday rates, "
            "soft trade, or an intentional roster choice."
        )
    return None


def _basis_lead(basis: list[dict]) -> str | None:
    if not basis:
        return None
    parts: list[str] = []
    for source in basis[:3]:
        label = source.get("label") or source.get("source") or "Source"
        latest = format_local_date(source.get("latest_date"))
        parts.append(f"{label} through {latest}")
    return "; ".join(parts)


def _warning_lines(question: str, context: dict) -> tuple[list[str], list[dict]]:
    components = data_health_components(context)
    required_sources = required_data_sources(question)
    currentness_sensitive = question_has_currentness(question)

    warnings: list[str] = []
    applied: list[dict] = []
    for source in required_sources:
        component = components.get(source)
        if not component:
            continue
        status = str(component.get("status") or "unknown").lower()
        latest = format_local_date(component.get("latest_date"))
        age_days = component.get("age_days")
        age_text = f" ({age_days} days old)" if age_days is not None else ""
        if status == "red" or (currentness_sensitive and status == "yellow"):
            warnings.append(f"{source_label(source)} latest {latest}{age_text}.")
            applied.append(
                {
                    "rule": "data_health_warning",
                    "category": "soft_warning",
                    "source": source,
                    "status": status,
                }
            )
    return warnings, applied


def _hard_block(question: str, context: dict) -> tuple[str, list[dict]] | None:
    components = data_health_components(context)
    quality_component = components.get("data_quality_flags") or {}
    active_flags = quality_component.get("active_flags") or []
    if not active_flags:
        return None

    relevant = bool(required_data_sources(question) or question_has_currentness(question))
    if not relevant:
        return None

    blockable_flags = [
        flag
        for flag in active_flags
        if str(flag.get("flag_type") or "") in {"partial_ingest", "manual_exclude_forecast"}
    ]
    if not blockable_flags:
        return None

    details = ", ".join(
        f"{flag.get('flag_type')}[{flag.get('severity', 'unknown')}] {flag.get('reason', '').strip()}"
        for flag in blockable_flags[:3]
    )
    message = (
        "I can't safely present that as a current answer because active data-quality flags indicate "
        "the underlying numbers may be incomplete or intentionally excluded.\n\n"
        f"Current issue: {details}"
    )
    applied = [
        {
            "rule": "data_quality_block",
            "category": "hard_blocker",
            "flag_type": flag.get("flag_type"),
            "severity": flag.get("severity"),
        }
        for flag in blockable_flags
    ]
    return message, applied


def compose_final_answer(
    question: str,
    context: dict,
    draft_answer: str,
) -> str:
    sections: list[str] = []
    basis = build_question_source_basis(question, context)
    lead = _basis_lead(basis)
    draft = (draft_answer or "").strip()

    if draft:
        if lead and "based on the latest available data" not in draft.lower():
            noun = "recommendation" if keyword_match(
                question,
                ["recommend", "focus", "should", "next", "improve", "opportunity"],
            ) else "answer"
            sections.append(f"Based on the latest available data ({lead}), here's my best {noun}:\n")
        sections.append(draft)

    return "\n".join(section for section in sections if section).strip()


def review_draft_answer(
    question: str,
    context: dict,
    messages: list[dict],
    draft_answer: str,
) -> ChatResponseEnvelope:
    block = _hard_block(question, context)
    if block is not None:
        block_message, applied_rules = block
        return ChatResponseEnvelope(
            draft_answer=draft_answer,
            final_answer=block_message,
            warnings=[],
            follow_up_questions=[],
            follow_up_hint=None,
            blocked=True,
            block_reason="data_quality_block",
            applied_rules=applied_rules,
        )

    warnings, applied_rules = _warning_lines(question, context)

    follow_up_questions: list[str] = []
    follow_up_hint: str | None = None
    curiosity_item = select_curiosity_item(question, context, messages)
    if curiosity_item is not None:
        follow_up_questions.append(
            str(curiosity_item.get("question") or curiosity_item.get("title") or "What should I learn next?")
        )
        follow_up_hint = curiosity_reply_hint(curiosity_item)
        applied_rules.append(
            {
                "rule": "curiosity_follow_up",
                "category": "missing_data_follow_up",
                "agenda_type": curiosity_item.get("agenda_type"),
                "source_gap_type": curiosity_item.get("source_gap_type"),
                "question": curiosity_item.get("question"),
            }
        )

    if build_question_source_basis(question, context):
        applied_rules.append(
            {
                "rule": "question_source_basis",
                "category": "enrichment",
            }
        )

    final_answer = compose_final_answer(
        question=question,
        context=context,
        draft_answer=draft_answer,
    )
    return ChatResponseEnvelope(
        draft_answer=draft_answer,
        final_answer=final_answer,
        warnings=warnings,
        follow_up_questions=follow_up_questions,
        follow_up_hint=follow_up_hint,
        blocked=False,
        block_reason=None,
        applied_rules=applied_rules,
    )
