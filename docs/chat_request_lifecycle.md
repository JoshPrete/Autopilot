# Chat Request Lifecycle

## Current design

The chat assistant now uses an AI-first-then-rules pipeline for normal question answering.

There is one intentional exception:
- explicit operator-rule capture messages such as `Milk delivery is Monday, Wednesday, Friday`
- explicit `confirm` / `reject` replies for pending rules

Those messages still short-circuit into the structured knowledge layer because they are state mutations, not answer generation.

## Before

```text
user message
-> operator rule parser
-> context retrieval
-> deterministic data-health gate
-> deterministic curiosity/follow-up gate
-> LLM
-> streamed response
```

This made the UX feel rules-first because stale-data and knowledge-gap checks could replace the answer entirely before the model had a chance to reason.

## After

```text
user message
->
request classification (direct lookup / analytical / strategic)
-> source selection
-> targeted data fetch
-> business metric computation when needed
-> operator rule parser (only for explicit save/confirm/reject intent)
-> LLM draft answer
-> rule review on {request, context, draft_answer}
-> final response assembly
-> SSE payload
```

## Request classes

- `direct_lookup`
  - factual source-of-truth questions such as `Who's working tomorrow?`
- `analytical`
  - multi-source business questions such as `Are we overstaffed tomorrow?`
- `strategic`
  - synthesis questions such as `What's the biggest operational risk this week?`

## Rule categories

The post-LLM review layer classifies rules into:

- `hard_blocker`
  - used only for true data-integrity failures such as active `partial_ingest` / `manual_exclude_forecast` flags
- `soft_warning`
  - stale or degraded source freshness
- `missing_data_follow_up`
  - one follow-up question that would materially improve future answers
- `enrichment`
  - source-basis metadata and other non-blocking context

## Response shape

Normal chat responses now carry a structured envelope:

```json
{
  "draft_answer": "...",
  "final_answer": "...",
  "warnings": [],
  "follow_up_questions": [],
  "blocked": false,
  "block_reason": null,
  "applied_rules": []
}
```

The UI still reads `content`, which is set to `final_answer`.

## UX expectations

- stale data should usually produce a caveated answer, not a refusal
- missing recipe or workflow logic should usually appear as a follow-up after the answer
- hard blocking should be rare and explainable
