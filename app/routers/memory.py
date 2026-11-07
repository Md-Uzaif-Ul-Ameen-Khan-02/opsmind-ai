"""Hindsight memory explorer: status, recall, reflect, retained knowledge."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_memory_service
from ..models import AgentActivity
from ..schemas import (
    ActivityOut,
    MemoryListResponse,
    MemoryRecallRequest,
    MemoryRecallResponse,
    MemoryReflectRequest,
    MemoryReflectResponse,
    MemoryStatusOut,
)
from ..services.activity import record_activity
from ..services.analytics import memory_status_out
from ..services.memory import MemoryService
from ..services.memory_links import enrich_memories, enrich_retained
from ..services.seeding import sync_verified_knowledge
from ..services.serializers import activity_out

router = APIRouter(prefix="/memory", tags=["memory"])

MEMORY_OPERATIONS = ("recall", "retain", "reflect")


@router.get("/status", response_model=MemoryStatusOut)
def status(
    probe: bool = Query(default=True),
    memory: MemoryService = Depends(get_memory_service),
) -> MemoryStatusOut:
    return memory_status_out(memory, probe=probe)


@router.post("/recall", response_model=MemoryRecallResponse)
def recall(
    payload: MemoryRecallRequest,
    db: Session = Depends(get_db),
    memory: MemoryService = Depends(get_memory_service),
) -> MemoryRecallResponse:
    outcome = memory.recall(payload.query, max_tokens=payload.max_tokens)
    memories = enrich_memories(db, outcome.results)
    record_activity(
        db,
        operation="recall",
        status="skipped"
        if outcome.status == "not_configured"
        else ("error" if outcome.status == "error" else "success"),
        backend="hindsight",
        query=payload.query,
        result_count=len(memories),
        duration_ms=outcome.duration_ms,
        detail=f"Manual recall against Hindsight bank {memory.cfg.hindsight_bank_id}.",
        error=outcome.error,
    )
    db.commit()
    return MemoryRecallResponse(
        query=payload.query,
        results=memories,
        status=outcome.status,
        memory_backend="hindsight",
        bank_id=memory.cfg.hindsight_bank_id,
        duration_ms=outcome.duration_ms,
        error=outcome.error,
    )


@router.post("/reflect", response_model=MemoryReflectResponse)
def reflect(
    payload: MemoryReflectRequest,
    db: Session = Depends(get_db),
    memory: MemoryService = Depends(get_memory_service),
) -> MemoryReflectResponse:
    outcome = memory.reflect(payload.query, context=payload.context)
    record_activity(
        db,
        operation="reflect",
        status="success" if outcome.status == "ok" else (
            "skipped" if outcome.status == "skipped" else "error"
        ),
        backend="hindsight",
        query=payload.query,
        duration_ms=outcome.duration_ms,
        detail=f"Manual reflect against Hindsight bank {memory.cfg.hindsight_bank_id}.",
        error=outcome.error,
    )
    db.commit()
    return MemoryReflectResponse(
        query=payload.query,
        text=outcome.text,
        status=outcome.status,
        error=outcome.error,
        duration_ms=outcome.duration_ms,
    )


@router.get("/memories", response_model=MemoryListResponse)
def list_memories(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    memory: MemoryService = Depends(get_memory_service),
) -> MemoryListResponse:
    status_value, items, total, error = memory.list_memories(limit=limit, offset=offset)
    return MemoryListResponse(
        bank_id=memory.cfg.hindsight_bank_id,
        items=enrich_retained(db, items),
        total=total,
        status=status_value,
        error=error,
    )


@router.get("/activity", response_model=list[ActivityOut])
def activity(
    limit: int = Query(default=40, ge=1, le=200),
    incident_id: int | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[ActivityOut]:
    query = (
        db.query(AgentActivity)
        .options(selectinload(AgentActivity.incident))
        .filter(AgentActivity.operation.in_(MEMORY_OPERATIONS))
    )
    if incident_id is not None:
        query = query.filter(AgentActivity.incident_id == incident_id)
    rows = query.order_by(AgentActivity.created_at.desc(), AgentActivity.id.desc()).limit(limit).all()
    return [activity_out(row) for row in rows]


@router.post("/sync")
def sync(
    db: Session = Depends(get_db),
    memory: MemoryService = Depends(get_memory_service),
) -> dict:
    """Retain verified resolutions that are not yet in Hindsight."""
    return sync_verified_knowledge(db, memory)
