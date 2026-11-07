"""Audit trail helpers for agent / memory operations."""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import AgentActivity


def record_activity(
    db: Session,
    *,
    operation: str,
    status: str,
    detail: str,
    backend: str = "hindsight",
    incident_id: int | None = None,
    query: str | None = None,
    result_count: int | None = None,
    duration_ms: int | None = None,
    error: str | None = None,
) -> AgentActivity:
    """Queue an activity row. The caller owns the transaction/commit."""
    activity = AgentActivity(
        incident_id=incident_id,
        operation=operation,
        status=status,
        backend=backend,
        detail=detail,
        query=query,
        result_count=result_count,
        duration_ms=duration_ms,
        error=error,
    )
    db.add(activity)
    return activity
