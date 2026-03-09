"""
Action engine — Decision layer.

Converts intelligence signals into 3–5 concrete operator actions.
Uses deterministic rule logic: no ML, no LLM required.

Rules are evaluated in priority order; a maximum of 5 actions are returned.
Each rule fires independently — they do not block each other.
"""

from __future__ import annotations

from datetime import datetime


def generate_actions(signals: dict) -> list[str]:
    """
    Convert signals into 3–5 operator actions for tomorrow.

    Args:
        signals: output dict from the intelligence layer, containing:
            predicted_drinks (int)
            predicted_cents (int)
            rush_windows (list[dict])
            labor_risk ("green" / "amber" / "red")
            wage_pct (float)
            staff_count (int)
            total_hours (float)

    Returns:
        list of 3–5 action strings, ordered by priority
    """
    actions: list[str] = []

    rush_windows = signals.get("rush_windows", [])
    labor_risk = signals.get("labor_risk", "green")
    wage_pct = float(signals.get("wage_pct") or 0)
    predicted_drinks = int(signals.get("predicted_drinks") or 0)
    staff_count = int(signals.get("staff_count") or 0)

    # Sort windows earliest-first for consistent messaging
    sorted_windows = sorted(rush_windows, key=lambda w: w.get("start", ""))
    first_rush = sorted_windows[0] if sorted_windows else None
    last_rush = sorted_windows[-1] if sorted_windows else None
    peak_windows = [w for w in sorted_windows if w.get("band") in ("peak", "heavy")]

    # ── Rule 1: Pre-rush prep timing ─────────────────────────────────────────
    # Fire if there is any rush window
    if first_rush:
        start_str = _fmt_time(first_rush["start"])
        actions.append(
            f"Start prep 15 min before {start_str} — "
            "stage cups, batch milk, pull shots ahead of rush"
        )

    # ── Rule 2: Peak staffing ─────────────────────────────────────────────────
    # Fire if any window is peak or heavy band
    if peak_windows:
        w = peak_windows[0]
        start = _fmt_time(w["start"])
        end = _fmt_time(w["end"])
        drinks = w.get("predicted_drinks", "")
        if staff_count < 3:
            actions.append(
                f"Schedule second barista {start}–{end} "
                f"({drinks} drinks forecast — cover needed)"
            )
        else:
            actions.append(
                f"Confirm second barista on espresso {start}–{end} "
                f"({drinks} drinks — peak band)"
            )

    # ── Rule 3: Filter batch brew ─────────────────────────────────────────────
    # Fire on high-volume days (≥250 drinks) — prep filter early
    if predicted_drinks >= 250 and first_rush:
        prep_time = _subtract_minutes(_fmt_time(first_rush["start"]), 30)
        actions.append(f"Batch brew filter coffee at {prep_time} before morning rush")

    # ── Rule 4: Food prep lead time ───────────────────────────────────────────
    # Fire on very high-volume days (≥300 drinks)
    if predicted_drinks >= 300:
        actions.append(
            "Start food prep 15 minutes earlier — "
            f"high-volume day ({predicted_drinks} drinks forecast)"
        )

    # ── Rule 5: Labor risk response ───────────────────────────────────────────
    if labor_risk == "red":
        actions.append(
            f"Trim 1 labor-hour from lowest-load block "
            f"(wage% at {wage_pct:.0f}% — target <35%)"
        )
    elif labor_risk == "amber":
        actions.append(
            f"Re-time one start to peak period rather than adding hours "
            f"(wage% {wage_pct:.0f}%)"
        )

    # ── Rule 6: Post-rush labor monitoring ────────────────────────────────────
    # Fire if wage% is high enough to warrant watching
    if wage_pct > 28 and last_rush:
        cutoff = _fmt_time(last_rush["end"])
        actions.append(
            f"Watch labour % after {cutoff} — "
            "cut a shift if pace slows below target"
        )
    elif wage_pct > 28:
        actions.append("Watch labour % after 11:30 — cut if pace slows")

    # ── Ensure minimum 3 actions ──────────────────────────────────────────────
    if len(actions) < 3:
        actions.append("Run equipment check 30 min before open — confirm grinder settings")
    if len(actions) < 3:
        actions.append("Confirm all mise en place staged the evening before")

    return actions[:5]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_time(iso_value: str | None) -> str:
    """Convert ISO datetime string to HH:MM."""
    if not iso_value:
        return "unknown"
    try:
        cleaned = str(iso_value).replace("Z", "+00:00")
        dt = datetime.fromisoformat(cleaned)
        return dt.strftime("%H:%M")
    except ValueError:
        return str(iso_value)


def _subtract_minutes(hhmm: str, minutes: int) -> str:
    """Subtract `minutes` from a HH:MM string, return HH:MM."""
    try:
        h, m = map(int, hhmm.split(":"))
        total = h * 60 + m - minutes
        total = max(0, total)
        return f"{total // 60:02d}:{total % 60:02d}"
    except Exception:
        return hhmm
