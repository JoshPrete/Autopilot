#!/usr/bin/env python3
"""Operator CLI for reviewing Xero mapping proposals and quarantines."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from typing import Optional

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")

from data.storage import (
    get_xero_line_mapping_by_id,
    get_xero_review_counts,
    list_xero_review_queue,
    resolve_xero_review_item,
    resolve_xero_review_items_for_mapping,
    update_xero_line_mapping_status,
)


def _parse_since(value: str) -> Optional[datetime]:
    raw = (value or "").strip().lower()
    if not raw:
        return None
    if raw.endswith("d"):
        return datetime.now(timezone.utc) - timedelta(days=max(0, int(raw[:-1] or "0")))
    if raw.endswith("h"):
        return datetime.now(timezone.utc) - timedelta(hours=max(0, int(raw[:-1] or "0")))
    raise ValueError("--since must be like '7d' or '24h'")


def _print_rows(rows: list[dict]) -> None:
    if not rows:
        print("No review items.")
        return
    print(
        f"{'ID':>5}  {'Reason':<18} {'Invoice':<14} {'Supplier':<18} "
        f"{'Suggested':<22} {'Conf':<5} {'Created':<20} Description"
    )
    print("-" * 128)
    for row in rows:
        supplier = str(row.get("supplier") or "")[:18]
        invoice = str(row.get("invoice_number") or row.get("invoice_id") or "")[:14]
        suggested = str(row.get("suggested_score_key") or "")[:22]
        conf = row.get("suggested_confidence")
        conf_text = f"{float(conf):.2f}" if conf is not None else "-"
        created = str(row.get("created_at") or "")[:19]
        desc = str(row.get("line_description") or "")
        if len(desc) > 70:
            desc = desc[:67] + "..."
        print(
            f"{int(row.get('review_id') or 0):>5}  "
            f"{str(row.get('reason_code') or ''):<18} "
            f"{invoice:<14} {supplier:<18} {suggested:<22} {conf_text:<5} {created:<20} {desc}"
        )


def _cmd_list(args: argparse.Namespace) -> int:
    since = _parse_since(args.since)
    rows = list_xero_review_queue(
        site_id=args.site_id,
        since=since,
        queue_status="open",
        limit=1000,
    )
    counts = get_xero_review_counts(args.site_id, queue_status="open")
    total = int(sum(counts.values()))
    print(f"Open review items: {total}")
    if counts:
        print(
            "By reason: "
            + ", ".join(f"{reason}={count}" for reason, count in sorted(counts.items()))
        )
    _print_rows(rows)
    return 0


def _cmd_approve(args: argparse.Namespace) -> int:
    mapping = get_xero_line_mapping_by_id(args.site_id, args.mapping_id)
    if not mapping:
        print(f"Mapping {args.mapping_id} not found for site {args.site_id}")
        return 1
    score_key = args.score_key or mapping.get("score_key")
    if not score_key:
        print("approve requires --score-key when mapping has no score_key")
        return 1
    updated = update_xero_line_mapping_status(
        site_id=args.site_id,
        mapping_id=args.mapping_id,
        status="approved",
        score_key=score_key,
        approved_by=args.actor,
    )
    if not updated:
        print(f"Failed to approve mapping {args.mapping_id}")
        return 1
    print(
        f"Approved mapping {args.mapping_id}: '{mapping.get('xero_description')}' -> {score_key} "
        f"(approved_by={args.actor})"
    )
    return 0


def _cmd_reject(args: argparse.Namespace) -> int:
    mapping = get_xero_line_mapping_by_id(args.site_id, args.mapping_id)
    if not mapping:
        print(f"Mapping {args.mapping_id} not found for site {args.site_id}")
        return 1
    updated = update_xero_line_mapping_status(
        site_id=args.site_id,
        mapping_id=args.mapping_id,
        status="rejected",
        approved_by=args.actor,
    )
    if not updated:
        print(f"Failed to reject mapping {args.mapping_id}")
        return 1
    print(f"Rejected mapping {args.mapping_id}: '{mapping.get('xero_description')}'")
    return 0


def _cmd_apply(args: argparse.Namespace) -> int:
    mapping = get_xero_line_mapping_by_id(args.site_id, args.mapping_id)
    if not mapping:
        print(f"Mapping {args.mapping_id} not found for site {args.site_id}")
        return 1
    if str(mapping.get("status") or "") != "approved":
        print(
            f"Mapping {args.mapping_id} is not approved (status={mapping.get('status')}). "
            "Run approve first."
        )
        return 1

    resolved = resolve_xero_review_items_for_mapping(
        site_id=args.site_id,
        mapping_id=args.mapping_id,
        resolved_by=args.actor,
        resolution_note=(
            "Operator apply: mapping approved; guarded cost updates occur on next Xero sync."
        ),
    )
    print(
        f"Applied mapping {args.mapping_id} ({mapping.get('score_key')}) for future syncs. "
        f"Resolved {resolved} linked review item(s)."
    )
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Xero review queue and mapping workflow CLI.")
    parser.add_argument("--site-id", required=True, help="Site UUID")
    parser.add_argument(
        "--actor",
        default="cli",
        help="Operator identifier stored in approval/resolution metadata",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="List outstanding review items")
    p_list.add_argument("--since", default="7d", help="Lookback window, e.g. 7d (default) or 24h")
    p_list.set_defaults(func=_cmd_list)

    p_approve = sub.add_parser("approve", help="Approve a mapping proposal")
    p_approve.add_argument("--mapping-id", type=int, required=True, help="Mapping row id")
    p_approve.add_argument("--score-key", help="Optional score_key override when approving")
    p_approve.set_defaults(func=_cmd_approve)

    p_reject = sub.add_parser("reject", help="Reject a mapping proposal")
    p_reject.add_argument("--mapping-id", type=int, required=True, help="Mapping row id")
    p_reject.set_defaults(func=_cmd_reject)

    p_apply = sub.add_parser(
        "apply",
        help="Resolve pending review items for an approved mapping (DB-only operation)",
    )
    p_apply.add_argument("--mapping-id", type=int, required=True, help="Mapping row id")
    p_apply.set_defaults(func=_cmd_apply)

    p_resolve = sub.add_parser("resolve", help="Resolve one review queue item by id")
    p_resolve.add_argument("--review-id", type=int, required=True, help="Review row id")
    p_resolve.set_defaults(func=_cmd_resolve)

    return parser


def _cmd_resolve(args: argparse.Namespace) -> int:
    ok = resolve_xero_review_item(
        site_id=args.site_id,
        review_id=args.review_id,
        resolved_by=args.actor,
        resolution_note="Resolved manually via CLI.",
    )
    if not ok:
        print(f"Review item {args.review_id} not found/open")
        return 1
    print(f"Resolved review item {args.review_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
