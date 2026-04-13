# Clubhouse Autopilot — Review Notes (2026-03-26)

Prepared for Claude Code to act on. Read alongside `README.md`, `CONTEXT.md`, `STATE.md`, and `TASK.md`.

---

## What's Been Built (Summary)

The project is in good shape structurally. The pipeline spine (ingest → deputy → profitability → xero → predict → intelligence) is well-defined, the demo path works, and the knowledge/chat layer is taking shape. The active task (`2026-03-13-chat-rule-reliability`) correctly targets the highest-priority gap: making rule capture reliable before expanding features.

The architecture documentation in `README.md`, `CONTEXT.md`, `AGENTS.md`, `STATE.md`, and `TASK.md` is unusually thorough for a project at this stage — these should be kept current as the active standard for agent behaviour.

---

## Active Task Status

**Task:** `2026-03-13-chat-rule-reliability`
**Objective:** Chat-captured operating rules reliably parse, persist, confirm, and reappear in grounded context.

The rule flow (`propose → confirm → recall`) is architecturally complete:
- `app/operator_knowledge.py` parses messages into structured rule proposals
- `app/chat.py` → `_handle_operator_rule_message()` orchestrates the three-state flow
- `data/storage.py` handles `create_operator_rule`, `confirm_operator_rule`, `reject_operator_rule`, and `get_pending_operator_rule`
- Confirmed rules are loaded into chat context via `list_operator_rules(statuses=["confirmed"])`

The code structure is correct. The outstanding risk is the one documented in `STATE.md`: if the DB role lacks `CREATE` on `public`, every rule storage call silently returns `None`. The chat now surfaces this as an error message rather than silently accepting, which is the right behaviour.

**Before doing anything else: verify the DB and confirm `operator_rules` exists.** See `VERIFY.md` for the exact commands.

---

## Specific Issues to Address

### 1. `_ensure_operator_rules_table` is called on every DB operation (performance + noise)

**File:** `data/storage.py`, line 2629
**Issue:** Every call to `create_operator_rule`, `get_pending_operator_rule`, `list_operator_rules`, `confirm_operator_rule`, and `reject_operator_rule` issues a `CREATE TABLE IF NOT EXISTS` DDL statement against the DB. This is:
- Wasteful under normal operation (the table exists after the first call)
- Noisy in logs under a restricted DB role (every call logs a warning)
- A source of subtle transaction state issues (DDL inside a connection that may already be in a transaction)

**Recommendation:** Add a module-level boolean flag (`_operator_rules_table_verified = False`) and only call `_ensure_operator_rules_table` once per process startup — or better, make the table creation part of the Alembic migration path (`migrations/`) so it exists before the app runs, and remove the inline `CREATE TABLE IF NOT EXISTS` guard entirely. The `_ensure_operator_rules_table` guard was a workaround for the schema permission issue; the proper fix is to grant the app DB role `CREATE` on `public` and run the migration.

---

### 2. `list_operator_rules` status filter runs in Python after the DB query

**File:** `data/storage.py`, line 2788
**Issue:** The `statuses` parameter is applied as a Python list filter _after_ all rows are fetched from the DB (line 2830–2832). For a small dataset this is fine, but it bypasses the index `idx_operator_rules_site_status` and fetches all rows unnecessarily.

**Recommendation:** Move the `statuses` filter into the SQL `WHERE` clause. The current query already orders by status, so the index can be used. Something like:
```sql
AND (:statuses IS NULL OR status = ANY(:statuses))
```
This is a minor efficiency fix; don't do it in the same task as the active rule-reliability work unless it's trivial.

---

### 3. `get_pending_operator_rule` has no session isolation

**File:** `data/storage.py`, line 2756
**Issue:** The "pending rule" concept is per `site_id` only — it returns the most recently proposed rule for the site. In a multi-user scenario (two staff members chatting simultaneously), one person's `confirm` message could confirm a rule proposed by someone else.

**Current risk level:** Low — the business currently has one operator using chat at a time.
**Recommendation:** Document this assumption explicitly in `STATE.md`. If multi-user chat ever becomes a real scenario, the pending rule will need a `session_id` or `chat_user_id` discriminator. Don't build this now — just record the assumption.

---

### 4. `CLAUDE_MODEL` is outdated

**File:** `app/chat.py`, line 81
**Current value:** `claude-sonnet-4-5-20250929`
**Issue:** This model string is stale. The chat streaming will silently fall back or error depending on the API version.
**Recommendation:** Update to the current production model string. Check `config/settings.py` to see if this should be driven from an environment variable (`ANTHROPIC_MODEL`) rather than hardcoded — that would make upgrades easier.

---

### 5. No test covers the DB permission failure path

**File:** `tests/test_chat_rule_capture.py`
**Issue:** The existing tests mock `create_operator_rule` to return a valid rule. There is no test for the case where `create_operator_rule` returns `None` (i.e., the DB is unavailable or permissions block table creation). This is the exact failure mode documented in `STATE.md`.
**Recommendation:** Add a test that monkeypatches `create_operator_rule` to return `None` and asserts that the chat response contains the actionable failure message ("could not persist it... Check database access for operator_rules").

---

### 6. `confirm_operator_rule` has a dead code path

**File:** `data/storage.py`, line 2839
**Issue:** The function signature has `rule_id: str = None` and contains a fallback that fetches the latest proposed rule when `rule_id` is None. However, `_handle_operator_rule_message` in `chat.py` always passes `rule_id` from the fetched pending rule. The `rule_id=None` path is never reached in production.
**Recommendation:** Either make `rule_id` a required argument (cleaner), or add a comment explaining the fallback is intentional for direct API calls. Low priority.

---

## Architecture Observations (No Immediate Action Required)

**The knowledge layer direction is correct.** The `observe → explain → recommend → learn → retain` loop described in `CONTEXT.md` is the right model for the product Josh is building. The current parsers cover the most important rule types (delivery, ordering, storage, recipes, staffing, COGS). The next highest-value types to add would be `menu_pricing_rule` (when and why prices change) and `seasonal_rule` (items only available certain periods).

**Chat grounding is the bottleneck, not intelligence.** The 5-phase intelligence cycle in `analysis/intelligence.py` is more sophisticated than the trust placed in it — operators will doubt AI-generated insights if the underlying rules are wrong or missing. Fixing rule persistence and recall (the active task) unlocks the intelligence layer's credibility. Keep the task prioritisation as-is.

**Curiosity questioning is well-positioned.** The curiosity and knowledge-gap detection layers (`analysis/curiosity.py`, `analysis/knowledge_gaps.py`) are the right tool for surfacing missing recipes and ordering schedules. These should be activated once rule persistence is confirmed reliable — otherwise they'll surface questions whose answers vanish.

**The Xero reconciliation model is sound.** The controlled enrichment model (proposed → approved, with delta clamp and IQR guardrails) is the right approach for COGS data that directly affects profitability calculations. The `xero_review_queue` with reason codes is good operational hygiene.

---

## What To Do Next (In Order)

1. Confirm the DB is accessible and `operator_rules` exists — run the `VERIFY.md` checks first.
2. Resolve the `_ensure_operator_rules_table` approach: migrate the table creation into Alembic so the guard is only a safety net, not the primary path.
3. Add the missing test for the DB-unavailable failure path (item 5 above).
4. Run the full VERIFY.md test suite and confirm it passes cleanly.
5. Update `CLAUDE_MODEL` in `app/chat.py` (item 4 above).
6. Update `STATE.md` to reflect resolved issues once verified.

---

## Files to Read Before Making Any Change

Per `AGENTS.md`:
1. `README.md`
2. `CONTEXT.md`
3. `STATE.md`
4. `TASK.md`
5. `VERIFY.md`

Scope for the active task is locked to: `app/chat.py`, `app/operator_knowledge.py`, `data/storage.py`, `tests/`. Do not touch `frontend/` or unrelated integrations.
