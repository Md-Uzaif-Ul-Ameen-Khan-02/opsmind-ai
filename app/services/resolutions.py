"""Engineer-in-the-loop resolution recording and Hindsight retention."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..models import Incident, IncidentResolution
from ..schemas import ResolutionCreate, ResolutionOut
from .activity import record_activity
from .errors import ConflictError
from .memory import RetainOutcome

RETAIN_RETRYABLE = {"failed", "skipped", "not_retained", "pending"}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def record_resolution(
    db: Session, incident: Incident, payload: ResolutionCreate
) -> IncidentResolution:
    """Store an engineer's root cause + fix WITHOUT verification or retention."""
    existing = incident.resolution
    if existing is not None and existing.verified:
        raise ConflictError(
            "This incident already has a verified resolution. Verified knowledge is locked so "
            "Hindsight memory cannot diverge from the confirmed record."
        )

    if existing is None:
        resolution = IncidentResolution(
            incident_id=incident.id,
            root_cause=payload.root_cause,
            resolution_summary=payload.resolution_summary,
            actions_taken=payload.actions_taken,
            lessons_learned=payload.lessons_learned,
            verification_notes=payload.verification_notes,
            verified=False,
            retain_status="not_retained",
        )
        db.add(resolution)
    else:
        resolution = existing
        resolution.root_cause = payload.root_cause
        resolution.resolution_summary = payload.resolution_summary
        resolution.actions_taken = payload.actions_taken
        resolution.lessons_learned = payload.lessons_learned
        resolution.verification_notes = payload.verification_notes
        resolution.verified = False
        resolution.retain_status = "not_retained"
        resolution.retain_error = None
        resolution.verified_at = None
        resolution.verified_by = None

    if incident.status == "open":
        incident.status = "investigating"

    record_activity(
        db,
        operation="resolve",
        status="success",
        backend="system",
        incident_id=incident.id,
        detail=(
            "Engineer recorded a root cause and fix. Status is UNVERIFIED — nothing was written "
            "to Hindsight until confirmation."
        ),
    )
    db.commit()
    db.refresh(resolution)
    return resolution


def confirm_resolution(
    db: Session,
    incident: Incident,
    payload: ResolutionCreate,
    *,
    memory_service,
) -> tuple[IncidentResolution, RetainOutcome, bool]:
    """Mark the resolution verified and retain the knowledge in Hindsight.

    Returns (resolution, retain_outcome, retry_only).
    """
    existing = incident.resolution
    retry_only = False

    if existing is not None and existing.verified:
        if existing.retain_status == "retained":
            raise ConflictError(
                "This resolution is already verified and retained in Hindsight. It cannot be "
                "edited; create a new incident if circumstances changed."
            )
        # Verified but retention failed earlier: retry retention only.
        resolution = existing
        retry_only = True
    elif existing is None:
        resolution = IncidentResolution(
            incident_id=incident.id,
            root_cause=payload.root_cause,
            resolution_summary=payload.resolution_summary,
            actions_taken=payload.actions_taken,
            lessons_learned=payload.lessons_learned,
            verification_notes=payload.verification_notes,
        )
        db.add(resolution)
    else:
        resolution = existing
        resolution.root_cause = payload.root_cause
        resolution.resolution_summary = payload.resolution_summary
        resolution.actions_taken = payload.actions_taken
        resolution.lessons_learned = payload.lessons_learned
        resolution.verification_notes = payload.verification_notes

    if not retry_only:
        verified_at = _now()
        resolution.verified = True
        resolution.verified_by = payload.verified_by
        resolution.verified_at = verified_at
        resolution.retain_status = "pending"
        resolution.retain_error = None
        incident.status = "resolved"
        incident.resolved_at = verified_at
        record_activity(
            db,
            operation="resolve",
            status="success",
            backend="system",
            incident_id=incident.id,
            detail=(
                f"Resolution verified by {payload.verified_by}. Preparing verified knowledge for "
                "Hindsight retention."
            ),
        )
        db.flush()

    retain = memory_service.retain_resolution(incident, resolution, resolution.verified_by)
    resolution.retain_status = retain.status
    resolution.retain_error = retain.error
    resolution.retained_at = _now() if retain.status == "retained" else None
    resolution.memory_document_id = retain.document_id if retain.status == "retained" else None
    resolution.memory_bank_id = (
        memory_service.cfg.hindsight_bank_id if retain.status == "retained" else None
    )

    record_activity(
        db,
        operation="retain",
        status="success" if retain.status == "retained" else (
            "skipped" if retain.status == "skipped" else "error"
        ),
        backend="hindsight",
        incident_id=incident.id,
        result_count=retain.items_count,
        duration_ms=retain.duration_ms,
        detail=(
            f"Retain confirmed resolution for {incident.public_id} into Hindsight bank "
            f"{memory_service.cfg.hindsight_bank_id}."
        ),
        error=retain.error,
    )
    db.commit()
    db.refresh(resolution)
    db.refresh(incident)
    return resolution, retain, retry_only


def resolution_out(resolution: IncidentResolution) -> ResolutionOut:
    return ResolutionOut.model_validate(resolution)


def resolution_snapshot(resolution: IncidentResolution | None) -> dict:
    if resolution is None:
        return {}
    return json.loads(
        json.dumps(
            {
                "verified": resolution.verified,
                "retain_status": resolution.retain_status,
                "verified_by": resolution.verified_by,
            }
        )
    )
