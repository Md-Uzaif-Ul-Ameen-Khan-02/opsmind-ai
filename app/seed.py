"""CLI seeding for OpsMind AI.

Usage:
    python -m app.seed                 # seed DB if empty, then retain verified knowledge
    python -m app.seed --reset         # wipe local incident records and reseed
    python -m app.seed --no-memory     # seed the database only
    python -m app.seed --memory-only   # retain existing verified resolutions in Hindsight
"""

from __future__ import annotations

import argparse
import sys

from .config import settings
from .database import SessionLocal, init_db
from .services.memory import MemoryService
from .services.seeding import seed_database, sync_verified_knowledge


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed OpsMind demonstration data")
    parser.add_argument("--reset", action="store_true", help="Delete local incident records first")
    parser.add_argument("--no-memory", action="store_true", help="Do not write to Hindsight")
    parser.add_argument(
        "--memory-only", action="store_true", help="Only retain verified resolutions in Hindsight"
    )
    args = parser.parse_args()

    init_db()
    with SessionLocal() as db:
        if not args.memory_only:
            result = seed_database(db, reset=args.reset)
            print(f"[seed] {result.get('message')}")
            print(f"[seed] incidents in database: {result.get('incident_count')}")

        if args.no_memory or args.memory_only and args.no_memory:
            print("[memory] skipped (--no-memory)")
            return 0

        memory = MemoryService()
        if not memory.configured:
            print(
                "[memory] Hindsight is NOT configured. Nothing was retained and nothing is "
                "simulated. Set HINDSIGHT_API_KEY in .env, then re-run with --memory-only."
            )
            return 2 if args.memory_only else 0

        print(f"[memory] Hindsight endpoint: {settings.hindsight_endpoint}")
        print(f"[memory] bank: {settings.hindsight_bank_id}")
        result = sync_verified_knowledge(db, memory)
        print(f"[memory] {result.get('message')}")
        for error in result.get("errors", []):
            print(f"[memory] error: {error}")
        status = memory.status(probe=True, force=True)
        print(
            f"[memory] connected={status.connected} memories={status.memory_count} "
            f"error={status.error}"
        )
        return 0 if result.get("status") in {"ok", "not_configured"} else 1


if __name__ == "__main__":
    sys.exit(main())
