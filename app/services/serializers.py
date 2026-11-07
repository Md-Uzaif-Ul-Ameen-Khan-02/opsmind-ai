"""ORM → API schema serializers."""

from __future__ import annotations

from ..models import AgentActivity, Incident, Investigation
from ..schemas import (
    ActivityOut,
    IncidentDetail,
    IncidentSummary,
    InvestigationOut,
    ResolutionOut,
)


def latest_investigation(incident: Incident) -> Investigation | None:
    if not incident.investigations:
        return None
    return sorted(incident.investigations, key=lambda item: (item.created_at, item.id))[-1]


def incident_summary(incident: Incident) -> IncidentSummary:
    investigation = latest_investigation(incident)
    resolution = incident.resolution
    return IncidentSummary(
        id=incident.id,
        public_id=incident.public_id,
        title=incident.title,
        severity=incident.severity,  # type: ignore[arg-type]
        environment=incident.environment,
        service=incident.service,
        status=incident.status,  # type: ignore[arg-type]
        occurred_at=incident.occurred_at,
        created_at=incident.created_at,
        resolved_at=incident.resolved_at,
        is_synthetic=incident.is_synthetic,
        reported_by=incident.reported_by,
        latest_investigation_id=investigation.id if investigation else None,
        latest_investigation_confidence=investigation.confidence if investigation else None,
        root_cause=resolution.root_cause if resolution else None,
        resolution_summary=resolution.resolution_summary if resolution else None,
        resolution_verified=bool(resolution and resolution.verified),
        retain_status=resolution.retain_status if resolution else None,
        memory_count=investigation.memory_count if investigation else None,
    )


def incident_detail(incident: Incident) -> IncidentDetail:
    investigation = latest_investigation(incident)
    summary = incident_summary(incident)
    return IncidentDetail(
        **summary.model_dump(),
        description=incident.description,
        error_logs=incident.error_logs,
        deployment_info=incident.deployment_info,
        updated_at=incident.updated_at,
        resolution=ResolutionOut.model_validate(incident.resolution)
        if incident.resolution
        else None,
        latest_investigation=InvestigationOut.model_validate(investigation)
        if investigation
        else None,
        investigation_count=len(incident.investigations),
    )


def activity_out(activity: AgentActivity, incident_public_id: str | None = None) -> ActivityOut:
    return ActivityOut(
        id=activity.id,
        incident_id=activity.incident_id,
        operation=activity.operation,
        status=activity.status,
        backend=activity.backend,
        detail=activity.detail,
        query=activity.query,
        result_count=activity.result_count,
        duration_ms=activity.duration_ms,
        error=activity.error,
        created_at=activity.created_at,
        incident_public_id=incident_public_id
        or (activity.incident.public_id if activity.incident else None),
    )
