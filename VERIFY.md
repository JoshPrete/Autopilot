# Verification

This file defines the expected verification path for ACP agents working in
`/Users/joshuaprete/Documents/clubhouse-autopilot`.

## General Rules

- Run the smallest relevant checks first.
- Prefer focused tests for the changed area before broad suites.
- If a required check cannot run, say exactly why.
- Do not claim success without showing which commands passed.

## Current Task Verification

Current task:
- restore reliable chat rule capture and grounded rule recall

Minimum required checks:

```bash
./.venv/bin/pytest /Users/joshuaprete/Documents/clubhouse-autopilot/tests/test_chat_rule_capture.py -q
./.venv/bin/pytest /Users/joshuaprete/Documents/clubhouse-autopilot/tests/test_chat_context.py -q
./.venv/bin/pytest /Users/joshuaprete/Documents/clubhouse-autopilot/tests/test_operator_knowledge.py -q
./.venv/bin/ruff check /Users/joshuaprete/Documents/clubhouse-autopilot/app/chat.py /Users/joshuaprete/Documents/clubhouse-autopilot/app/operator_knowledge.py /Users/joshuaprete/Documents/clubhouse-autopilot/data/storage.py /Users/joshuaprete/Documents/clubhouse-autopilot/tests --config /Users/joshuaprete/Documents/clubhouse-autopilot/pyproject.toml
```

## Database Verification

If the task touches persistence, confirm the active DB path is real:

```bash
psql "$DATABASE_URL" -Atqc "SELECT current_database(), current_user, current_schema();"
```

If rule persistence is part of the task, verify the table exists:

```bash
psql "$DATABASE_URL" -Atqc "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename='operator_rules';"
```

Optional direct verification for saved rules:

```bash
psql "$DATABASE_URL" -Atqc "SELECT rule_type, rule_name, status FROM operator_rules ORDER BY created_at DESC LIMIT 10;"
```

## Failure Handling

If verification fails:

1. stop
2. report the exact failing command
3. report whether the failure is code, env, DB, or dependency related
4. do not continue into unrelated work

## Success Standard

A task is only complete when:

1. the requested behavior works
2. the required focused tests pass
3. lint passes on changed files
4. any DB or runtime assumptions are stated explicitly
