"""Demo database seeding and Hindsight knowledge synchronisation."""

from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from ..models import AgentActivity, Incident, IncidentResolution, Investigation, User
from .activity import record_activity
from .demo_data import (
    DEMO_SCENARIO_ONE,
    DEMO_SCENARIO_TWO,
    DEMO_USERS,
    build_historical_seed,
    build_scenario_incident,
    scenario_catalog,
    utc_now,
)
from .incidents import assign_public_id
from .memory import MemoryService

logger = logging.getLogger(__name__)


def ensure_users(db: Session) -> None:
    for user in DEMO_USERS:
        existing = db.query(User).filter(User.email == user["email"]).one_or_none()
        if existing is None:
            db.add(User(name=user["name"], email=user["email"], role=user["role"]))
    db.commit()


def _reset_records(db: Session) -> None:
    db.query(AgentActivity).delete(synchronize_session=False)
    db.query(IncidentResolution).delete(synchronize_session=False)
    db.query(Investigation).delete(synchronize_session=False)
    db.query(Incident).delete(synchronize_session=False)
    db.commit()


def _apply_public_id(incident: Incident) -> None:
    assign_public_id(incident)


def seed_database(db: Session, *, reset: bool = False) -> dict:
    """Seed the synthetic dataset. Idempotent unless ``reset`` is true."""
    if reset:
        _reset_records(db)

    existing = db.query(Incident).count()
    if existing and not reset:
        return {
            "seeded": False,
            "message": f"{existing} incidents already exist; seed skipped.",
            "incident_count": existing,
        }

    ensure_users(db)
    now = utc_now()

    for record in build_historical_seed(now):
        resolution_data = record["resolution"]
        incident = Incident(
            public_id="pending",
            title=record["title"],
            description=record["description"],
            severity=record["severity"],
            environment=record["environment"],
            service=record["service"],
            error_logs=record["error_logs"],
            deployment_info=record["deployment_info"],
            reported_by=record["reported_by"],
            status="resolved",
            is_synthetic=True,
            occurred_at=record["occurred_at"],
            created_at=record["created_at"],
            updated_at=record["resolved_at"],
            resolved_at=record["resolved_at"],
        )
        db.add(incident)
        db.flush()
        _apply_public_id(incident)
        db.add(
            IncidentResolution(
                incident_id=incident.id,
                root_cause=resolution_data["root_cause"],
                resolution_summary=resolution_data["resolution_summary"],
                actions_taken=resolution_data["actions_taken"],
                lessons_learned=resolution_data["lessons_learned"],
                verification_notes=resolution_data["verification_notes"],
                verified=True,
                verified_by=resolution_data["verified_by"],
                verified_at=resolution_data["verified_at"],
                retain_status="not_retained",
                created_at=record["created_at"],
                updated_at=record["resolved_at"],
            )
        )

    scenario = build_scenario_incident(DEMO_SCENARIO_ONE["key"], now)
    incident = Incident(
        public_id="pending",
        title=scenario["title"],
        description=scenario["description"],
        severity=scenario["severity"],
        environment=scenario["environment"],
        service=scenario["service"],
        error_logs=scenario["error_logs"],
        deployment_info=scenario["deployment_info"],
        reported_by=scenario["reported_by"],
        status="open",
        is_synthetic=True,
        occurred_at=scenario["occurred_at"],
        created_at=scenario["created_at"],
        updated_at=scenario["created_at"],
    )
    db.add(incident)
    db.flush()
    _apply_public_id(incident)

    record_activity(
        db,
        operation="seed",
        status="success",
        backend="system",
        detail=(
            "Seeded synthetic demonstration data: 12 verified historical incidents and the Act 1 "
            "payment incident. All records are labelled demonstration data."
        ),
    )
    db.commit()

    total = db.query(Incident).count()
    return {
        "seeded": True,
        "message": "Synthetic demonstration dataset created.",
        "incident_count": total,
        "verified_resolutions": 12,
        "memory_note": (
            "Verified resolutions are stored in SQLite. Hindsight retention is a separate, "
            "explicit step (POST /api/memory/sync) and is only marked retained after the real "
            "Hindsight retain call succeeds."
        ),
    }


def get_or_create_scenario(db: Session, key: str) -> tuple[Incident, bool]:
    source = DEMO_SCENARIO_ONE if key == DEMO_SCENARIO_ONE["key"] else DEMO_SCENARIO_TWO
    existing = db.query(Incident).filter(Incident.title == source["title"]).one_or_none()
    if existing is not None:
        return existing, False
    data = build_scenario_incident(key)
    incident = Incident(
        public_id="pending",
        title=data["title"],
        description=data["description"],
        severity=data["severity"],
        environment=data["environment"],
        service=data["service"],
        error_logs=data["error_logs"],
        deployment_info=data["deployment_info"],
        reported_by=data["reported_by"],
        status="open",
        is_synthetic=True,
        occurred_at=data["occurred_at"],
        created_at=data["created_at"],
        updated_at=data["created_at"],
    )
    db.add(incident)
    db.flush()
    _apply_public_id(incident)
    db.commit()
    db.refresh(incident)
    return incident, True


def sync_verified_knowledge(db: Session, memory_service: MemoryService) -> dict:
    """Retain every verified resolution not yet present in Hindsight.

    Only rows whose real Hindsight retain call succeeds are marked retained.
    """
    if not memory_service.configured:
        return {
            "status": "not_configured",
            "attempted": 0,
            "retained": 0,
            "failed": 0,
            "message": (
                "Hindsight is not configured (HINDSIGHT_API_KEY missing). No memory was written "
                "and nothing is simulated."
            ),
        }

    rows = (
        db.query(IncidentResolution)
        .join(Incident)
        .filter(
            IncidentResolution.verified.is_(True),
            IncidentResolution.retain_status != "retained",
        )
        .order_by(Incident.occurred_at.asc())
        .all()
    )

    retained = 0
    failed = 0
    errors: list[str] = []
    for resolution in rows:
        incident = resolution.incident
        if incident is None:
            continue
        outcome = memory_service.retain_resolution(
            incident, resolution, resolution.verified_by
        )
        resolution.retain_status = outcome.status
        resolution.retain_error = outcome.error
        resolution.retained_at = utc_now() if outcome.status == "retained" else None
        resolution.memory_document_id = (
            outcome.document_id if outcome.status == "retained" else None
        )
        resolution.memory_bank_id = (
            memory_service.cfg.hindsight_bank_id if outcome.status == "retained" else None
        )
        if outcome.status == "retained":
            retained += 1
        else:
            failed += 1
            if outcome.error and len(errors) < 5:
                errors.append(f"{incident.public_id}: {outcome.error}")
        record_activity(
            db,
            operation="retain",
            status="success" if outcome.status == "retained" else "error",
            backend="hindsight",
            incident_id=incident.id,
            result_count=outcome.items_count,
            duration_ms=outcome.duration_ms,
            detail=(
                f"Seed retention for verified resolution {incident.public_id} into Hindsight bank "
                f"{memory_service.cfg.hindsight_bank_id}."
            ),
            error=outcome.error,
        )
        db.commit()

    return {
        "status": "ok" if failed == 0 else "partial",
        "attempted": len(rows),
        "retained": retained,
        "failed": failed,
        "errors": errors,
        "message": (
            f"Retained {retained} verified resolution(s) in Hindsight"
            + (f"; {failed} failed." if failed else ".")
        ),
    }


def scenario_payloads() -> list[dict]:
    return scenario_catalog()
