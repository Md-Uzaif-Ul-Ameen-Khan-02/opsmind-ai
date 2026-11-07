"""Incident creation helpers."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Incident
from ..schemas import IncidentCreate


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def assign_public_id(incident: Incident) -> None:
    incident.public_id = f"INC-{3000 + incident.id:04d}"


def create_incident(db: Session, payload: IncidentCreate) -> Incident:
    now = utc_now()
    occurred_at = payload.occurred_at
    if occurred_at is not None and occurred_at.tzinfo is not None:
        occurred_at = occurred_at.astimezone(timezone.utc).replace(tzinfo=None)
    incident = Incident(
        public_id="pending",
        title=payload.title,
        description=payload.description,
        severity=payload.severity,
        environment=payload.environment,
        service=payload.service,
        error_logs=payload.error_logs,
        deployment_info=payload.deployment_info,
        reported_by=payload.reported_by,
        status="open",
        is_synthetic=payload.is_synthetic,
        occurred_at=occurred_at or now,
        created_at=now,
        updated_at=now,
    )
    db.add(incident)
    db.flush()
    assign_public_id(incident)
    db.commit()
    db.refresh(incident)
    return incident
