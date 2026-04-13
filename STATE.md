# Current State

This file is a short-lived snapshot of the repo/runtime situation so ACP agents
do not have to rediscover it each run.

## Current Product State

- The repo has a functioning ops spine and local demo path.
- The project is expanding into a structured knowledge and learning system.
- Chat can reason over a broad context, but trust is still limited by grounding
  and persistence issues.

## Current Strengths

- local install and focused test runs are workable
- tomorrow-plan demo flow exists
- profitability and inventory reasoning layers exist
- curiosity agenda and knowledge-gap detection exist
- chat can parse some operating rules

## Current Weaknesses

- chat grounding is not yet reliable enough
- rule persistence has recently failed due to DB/schema issues
- some live data paths are still fragile
- operators can see incorrect quoted data if storage or context is incomplete

## Recently Confirmed Runtime Issue

The active database originally had no `operator_rules` table because the app DB
role lacked `CREATE` on schema `public`.

Observed facts:

- active DB: `clubhouse_autopilot`
- app DB role: `user`
- `uuid-ossp` extension exists
- schema permission was the blocker, not UUID support

Implication:

- earlier chat rules may not have persisted at all
- chat could appear to accept rules without storing them

## Current Development Guidance

- Treat persistence and chat grounding as high priority.
- Do not assume a chat-captured rule exists unless it is verified in the DB.
- Prefer focused tests around the changed surface.
- Avoid overlapping work in the same files when running multiple agents.

## Multi-Agent Guidance

When multiple agents are working:

- one agent owns parser/capture files
- one agent owns downstream reasoning/explanation files
- avoid simultaneous edits to `app/chat.py` unless coordinated

## What To Check First

Before significant work:

1. `git status --short`
2. `psql "$DATABASE_URL" -Atqc "SELECT current_database(), current_user, current_schema();"`
3. required focused tests for the active task
