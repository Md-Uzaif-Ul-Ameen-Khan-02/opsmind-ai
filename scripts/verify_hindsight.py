"""Live verification of the Hindsight integration.

Requires a configured HINDSIGHT_API_KEY. This script talks to the real
Hindsight API (no stubs, no simulated memory) and prints exactly what came
back. By default it only reads (status, recall, reflect). Pass --retain-local
to retain every verified local resolution that is not yet in Hindsight.

Usage:
    cd backend
    python scripts/verify_hindsight.py
    python scripts/verify_hindsight.py --retain-local --query "payment api queuepool"
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402
from app.database import SessionLocal, init_db  # noqa: E402
from app.models import IncidentResolution  # noqa: E402
from app.services.memory import MemoryService  # noqa: E402
from app.services.seeding import sync_verified_knowledge  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the live Hindsight integration")
    parser.add_argument("--retain-local", action="store_true", help="Retain verified local resolutions first")
    parser.add_argument(
        "--query",
        default="Payment API connection pool exhaustion and queue pool timeout",
        help="Recall query for the read test",
    )
    args = parser.parse_args()

    print(f"Hindsight endpoint : {settings.hindsight_endpoint}")
    print(f"Memory bank        : {settings.hindsight_bank_id}")

    memory = MemoryService()
    if not memory.configured:
        print("\n[FAIL] HINDSIGHT_API_KEY is not configured.")
        print("Set it in .env, restart, then re-run this script. Nothing was simulated.")
        return 2

    init_db()
    status = memory.status(probe=True, force=True)
    print(f"Connected          : {status.connected}")
    print(f"Memories in bank   : {status.memory_count}")
    if status.error:
        print(f"Provider error     : {status.error}")
    if not status.connected:
        print("\n[FAIL] Hindsight is configured but unreachable.")
        return 1

    print(f"Ensuring bank      : {memory.ensure_bank()}")

    if args.retain_local:
        with SessionLocal() as db:
            unsynced = (
                db.query(IncidentResolution)
                .filter(
                    IncidentResolution.verified.is_(True),
                    IncidentResolution.retain_status != "retained",
                )
                .count()
            )
            print(f"Verified resolutions not yet retained: {unsynced}")
            result = sync_verified_knowledge(db, memory)
            print(f"Retain result      : {result.get('message')}")
            for error in result.get("errors", []):
                print(f"  error: {error}")
    else:
        with SessionLocal() as db:
            unsynced = (
                db.query(IncidentResolution)
                .filter(
                    IncidentResolution.verified.is_(True),
                    IncidentResolution.retain_status != "retained",
                )
                .count()
            )
        print(f"Verified resolutions not yet retained: {unsynced} (use --retain-local)")

    recall = memory.recall(args.query, max_tokens=4096)
    failed = recall.status == "error"
    print(f"\nRECALL status      : {recall.status} ({recall.duration_ms} ms)")
    print(f"RECALL query       : {args.query}")
    for index, item in enumerate(recall.results, start=1):
        print(f"\n  [{index}] id={item['memory_id']} score={item['score']} doc={item['document_id']}")
        print(f"      {item['text'][:600]}")
        if item.get("source_chunk"):
            print(f"      source chunk: {item['source_chunk'][:300]}...")
    if recall.error:
        print(f"RECALL error       : {recall.error}")
    if not recall.results:
        print("  (Hindsight returned zero relevant memories. Nothing was substituted.)")

    if recall.results:
        reflection = memory.reflect(
            "What do these confirmed incidents say about this failure mode, what fixed them "
            "and how relevant are they to a new payment API pool timeout?",
            context=args.query,
        )
        print(f"\nREFLECT status     : {reflection.status} ({reflection.duration_ms} ms)")
        print(reflection.text or f"(no text) {reflection.error}")
        failed = failed or reflection.status == "error"
    else:
        print("\nREFLECT skipped    : no memories were retrieved, so reflect was not attempted.")

    memory_status, items, total, error = memory.list_memories(limit=5)
    print(f"\nLIST MEMORIES      : {memory_status} total={total}")
    for item in items:
        print(f"  - {item['memory_id']} {item['document_id']} {item['type']}")
    if error:
        print(f"  error: {error}")

    if failed:
        print("\n[FAIL] A required Hindsight operation failed. Review the errors above.")
        return 1
    print("\n[DONE] Live Hindsight operations completed. Review the output above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
