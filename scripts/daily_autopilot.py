#!/usr/bin/env python3
"""
Clubhouse Autopilot - Daily runner

This is the entrypoint you run each day (or via scheduler/cron).
For now it:
- loads .env (if present)
- prints a clear status banner
- validates the critical environment variables exist
- exits with useful error codes

As you build Phase 1+, wire in:
- data.ingestion (Square Orders)
- data.processing (workload scoring)
- models.prediction + models.recommendations
- delivery.tomorrow_plan (PDF)
- delivery.sender (SMS)
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv


REQUIRED_ENV = [
    "SQUARE_ACCESS_TOKEN",
    "SQUARE_LOCATION_ID",
    "SQUARE_ENVIRONMENT",
    "DATABASE_URL",
]


def _project_root() -> Path:
    # scripts/ is one level below project root
    return Path(__file__).resolve().parents[1]


def load_env() -> None:
    root = _project_root()
    env_path = root / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        # still load from OS env if set
        load_dotenv()


def check_env(strict: bool = False) -> list[str]:
    missing = [k for k in REQUIRED_ENV if not os.getenv(k)]
    if strict and missing:
        print("\n❌ Missing required environment variables:")
        for k in missing:
            print(f"  - {k}")
        print("\nCreate a .env file from .env.example, then re-run.\n")
        sys.exit(2)
    return missing


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="daily_autopilot",
        description="Run Clubhouse Autopilot daily pipeline.",
    )
    p.add_argument(
        "--date",
        help="Business date to run (YYYY-MM-DD). Default: today.",
        default=str(date.today()),
    )
    p.add_argument(
        "--strict-env",
        action="store_true",
        help="Fail if required env vars are missing.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Run validations only; do not call external APIs or write outputs.",
    )
    return p.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    load_env()
    missing = check_env(strict=args.strict_env)

    print("\n=== Clubhouse Autopilot: Daily Runner ===")
    print(f"Project: {_project_root()}")
    print(f"Run date: {args.date}")
    print(f"Dry run: {args.dry_run}")
    if missing:
        print(f"⚠️  Missing env vars (non-strict): {', '.join(missing)}")
    else:
        print("✅ Environment looks OK")

    # TODO: Wire in the real pipeline steps here.
    # Example:
    # - orders = data.ingestion.fetch_orders(...)
    # - workload = data.processing.compute_workload(orders)
    # - recs = models.recommendations.generate(...)
    # - pdf_path = delivery.tomorrow_plan.render(...)
    # - delivery.sender.send_sms(...)
    print("✅ Runner executed (placeholder). Add pipeline steps next.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))


