---
task_id: 2026-03-13-chat-rule-reliability
title: Restore reliable chat rule capture and grounded rule recall
status: active
priority: high
repo: clubhouse-autopilot
owner: acp-agent
read_first:
  - /Users/joshuaprete/Documents/clubhouse-autopilot/README.md
  - /Users/joshuaprete/Documents/clubhouse-autopilot/CONTEXT.md
  - /Users/joshuaprete/Documents/clubhouse-autopilot/STATE.md
  - /Users/joshuaprete/Documents/clubhouse-autopilot/VERIFY.md
objective: >
  Make chat-captured operating rules reliably parse, persist, confirm, and
  reappear in grounded chat context so the assistant stops inventing or
  misquoting business rules.
scope:
  include:
    - app/chat.py
    - app/operator_knowledge.py
    - data/storage.py
    - tests/
    - README.md
    - TASK.md
    - VERIFY.md
    - CONTEXT.md
    - STATE.md
  exclude:
    - frontend/
    - deployment/
    - billing/
    - unrelated integrations
constraints:
  - Do not do a broad rewrite
  - Preserve the current chat and knowledge-layer architecture
  - Prefer deterministic parsing and explicit failure modes over vague AI behavior
  - Keep Square, Deputy, Xero, Twilio, and Anthropic optional/fail-quiet where possible
  - Do not touch unrelated dirty files
deliverables:
  - chat can parse a supported rule and save it as proposed
  - chat can confirm the pending rule and save it as confirmed
  - confirmed rules appear in gathered chat context
  - rule persistence failures are obvious and actionable
  - tests cover parse, save, confirm, and recall
verification:
  - command: ./.venv/bin/pytest /Users/joshuaprete/Documents/clubhouse-autopilot/tests/test_chat_rule_capture.py -q
  - command: ./.venv/bin/pytest /Users/joshuaprete/Documents/clubhouse-autopilot/tests/test_chat_context.py -q
  - command: ./.venv/bin/pytest /Users/joshuaprete/Documents/clubhouse-autopilot/tests/test_operator_knowledge.py -q
  - command: ./.venv/bin/ruff check /Users/joshuaprete/Documents/clubhouse-autopilot/app/chat.py /Users/joshuaprete/Documents/clubhouse-autopilot/app/operator_knowledge.py /Users/joshuaprete/Documents/clubhouse-autopilot/data/storage.py /Users/joshuaprete/Documents/clubhouse-autopilot/tests --config /Users/joshuaprete/Documents/clubhouse-autopilot/pyproject.toml
report_requirements:
  - root cause
  - files changed
  - commands run
  - verification result
  - remaining risks
blocked_if:
  - the active database is unreachable
  - schema permissions prevent persistence and cannot be fixed locally
  - the required parser behavior is ambiguous
on_blocked:
  - stop after diagnosis
  - report the blocker clearly
  - propose the smallest viable next action
---

# Context

The repo is evolving from a daily ops pipeline into a structured operating
intelligence system. Chat is no longer just a Q&A surface. It is also the input
layer for rules, recipes, staffing constraints, ordering schedules, and other
durable business logic.

# Problem

The current failure mode is high-severity:

1. Chat can appear to accept business rules without actually persisting them.
2. Confirmed rule recall is therefore incomplete or wrong.
3. The assistant then misquotes operational data or acts as though a rule exists
   when it does not.

This breaks trust quickly.

# Required Outcome

1. A supported message creates a proposed operating rule.
2. `confirm` saves that rule as confirmed.
3. Confirmed rules are visible in gathered chat context.
4. If parsing or persistence fails, the operator sees a clear failure, not a
   silent miss.

# Non-Goals

- Do not redesign the whole chat system.
- Do not add new frontend UI.
- Do not build new recommendation engines in this task.
- Do not expand into unrelated ACP orchestration code.

# Implementation Notes

- Check the active database before changing app logic.
- Treat parser output as a contract: stable keys, stable rule types, clear
  summaries.
- Prefer explicit storage checks over assuming the DB is correct.
- If the DB lacks required schema objects, either create them safely or report
  the exact SQL needed.
- The assistant must not imply that a rule is saved unless it is actually
  persisted.

# ACP Execution Contract

Every ACP agent should follow this order:

1. Read `README.md`
2. Read `CONTEXT.md`
3. Read `STATE.md`
4. Read this file
5. Make the smallest change that satisfies the objective
6. Run `VERIFY.md`
7. Return results in the format below

# Final Output Format

Return:

1. Summary
2. Root cause
3. Files changed
4. Commands run
5. Verification results
6. Remaining risks
