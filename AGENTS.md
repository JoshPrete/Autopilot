# AGENTS.md

Repo-local execution rules for ACP-based coding agents working in:

- `/Users/joshuaprete/Documents/clubhouse-autopilot`

This file is intended to make Claude/Codex-style agents behave consistently
inside this repo.

## Read Order

Before making changes, read files in this order:

1. `/Users/joshuaprete/Documents/clubhouse-autopilot/README.md`
2. `/Users/joshuaprete/Documents/clubhouse-autopilot/CONTEXT.md`
3. `/Users/joshuaprete/Documents/clubhouse-autopilot/STATE.md`
4. `/Users/joshuaprete/Documents/clubhouse-autopilot/TASK.md`
5. `/Users/joshuaprete/Documents/clubhouse-autopilot/VERIFY.md`

If `TASK.md` is missing, stop and ask for a task.

## Operating Principles

- Keep tasks narrow and verifiable.
- Prefer deterministic logic over vague AI behavior.
- Do not claim a business rule exists unless it is persisted and retrievable.
- Reliability is more important than feature expansion.
- Make the smallest coherent change that satisfies the active task.

## First Commands

Run these first unless the task explicitly says otherwise:

```bash
git status --short
```

If the task touches persistence or runtime state, also run:

```bash
psql "$DATABASE_URL" -Atqc "SELECT current_database(), current_user, current_schema();"
```

## Scope Discipline

- Only work on the files and directories allowed by `TASK.md`.
- Do not expand scope just because adjacent fixes are tempting.
- If you discover a broader structural issue, report it separately after finishing
  the active task or after being blocked.

## Multi-Agent Lane Rules

When multiple agents are active, avoid overlapping file families.

Recommended lane split:

- parser / capture lane
  - `/Users/joshuaprete/Documents/clubhouse-autopilot/app/operator_knowledge.py`
  - parser-focused tests
- chat / reasoning lane
  - `/Users/joshuaprete/Documents/clubhouse-autopilot/app/chat.py`
  - `/Users/joshuaprete/Documents/clubhouse-autopilot/analysis/`
- storage / schema lane
  - `/Users/joshuaprete/Documents/clubhouse-autopilot/data/storage.py`
  - `/Users/joshuaprete/Documents/clubhouse-autopilot/schema.sql`
  - `/Users/joshuaprete/Documents/clubhouse-autopilot/alembic/`
- UI lane
  - `/Users/joshuaprete/Documents/clubhouse-autopilot/frontend/`
  - `/Users/joshuaprete/Documents/clubhouse-autopilot/app/static/`

Rules:

- one owner per file family at a time
- do not edit the same file family as another active agent without coordination
- if a shared contract is needed, coordinate on payload shape, not simultaneous file edits

## Persistence Rules

If the task depends on saved rules, recommendations, or other DB-backed behavior:

1. verify the active DB
2. verify the required table exists
3. verify row creation or retrieval directly if needed

For chat rule capture specifically:

- do not assume a parsed rule is saved
- verify `operator_rules` exists if rule capture is failing
- if the table is missing or permissions block creation, report the exact DB fix

## Verification Rules

- Use `/Users/joshuaprete/Documents/clubhouse-autopilot/VERIFY.md` as the source of truth.
- Run focused tests on the changed area before broader checks.
- Do not claim success without listing the commands that passed.
- If a required check cannot run, state exactly why.

## Git Hygiene

- Never overwrite unrelated dirty changes.
- If unexpected modified files appear, stop and ask before proceeding.
- Do not do broad cleanup in the same task unless explicitly requested.
- Prefer small, logically scoped commits.

## Failure Handling

Stop and report clearly if:

- the active DB is unreachable
- schema permissions block required persistence
- required secrets or services are unavailable and no fallback exists
- the task contract is ambiguous or self-contradictory

When blocked:

1. state the blocker
2. state what you verified
3. propose the smallest viable next action

## Final Output

Unless `TASK.md` overrides it, return:

1. Summary
2. Root cause or key change
3. Files changed
4. Commands run
5. Verification results
6. Remaining risks
