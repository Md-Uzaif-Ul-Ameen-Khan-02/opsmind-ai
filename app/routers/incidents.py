"""Incident CRUD, investigation, engineer resolution and Hindsight retention."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_llm_service, get_memory_service, load_incident
from ..models import Incident
from ..schemas import (
    ActivityOut,
    ConfirmResolution,
    ConfirmResolutionResponse,
    IncidentCreate,
    IncidentDetail,
    IncidentPage,
    InvestigationOut,
    MemoryRetainResult,
    ResolutionCreate,
    ResolutionOut,
)
from ..services import agent
from ..services.analytics import SEVERITIES
from ..services.incidents import create_incident
from ..services.llm import GroqLLM
from ..services.memory import MemoryService
from ..services.resolutions import confirm_resolution, record_resolution
from ..services.serializers import activity_out, incident_detail, incident_summary

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=IncidentPage)
def list_incidents(
    q: str | None = Query(default=None, max_length=200),
    severity: str | None = Query(default=None),
    status: str | None = Query(default=None),
    service: str | None = Query(default=None, max_length=120),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> IncidentPage:
    query = db.query(Incident).options(
        selectinload(Incident.investigations), selectinload(Incident.resolution)
    )
    if q:
        pattern = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Incident.title.ilike(pattern),
                Incident.description.ilike(pattern),
                Incident.service.ilike(pattern),
                Incident.public_id.ilike(pattern),
                Incident.error_logs.ilike(pattern),
            )
        )
    if severity:
        values = [value.strip().lower() for value in severity.split(",") if value.strip()]
        allowed = [value for value in values if value in SEVERITIES]
        if allowed:
            query = query.filter(Incident.severity.in_(allowed))
    if status:
        values = [value.strip().lower() for value in status.split(",") if value.strip()]
        if values:
            query = query.filter(Incident.status.in_(values))
    if service:
        query = query.filter(Incident.service == service)

    total = query.count()
    rows = query.order_by(Incident.created_at.desc()).offset(offset).limit(limit).all()
    return IncidentPage(
        items=[incident_summary(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("", response_model=IncidentDetail, status_code=201)
def create(payload: IncidentCreate, db: Session = Depends(get_db)) -> IncidentDetail:
    incident = create_incident(db, payload)
    fresh = load_incident(db, incident.id)
    return incident_detail(fresh)


@router.get("/{incident_id}", response_model=IncidentDetail)
def get_incident(incident_id: str, db: Session = Depends(get_db)) -> IncidentDetail:
    return incident_detail(load_incident(db, incident_id))


@router.get("/{incident_id}/activity", response_model=list[ActivityOut])
def incident_activity(
    incident_id: str,
    limit: int = Query(default=40, ge=1, le=100),
    db: Session = Depends(get_db),
) -> list[ActivityOut]:
    incident = load_incident(db, incident_id)
    activities = sorted(incident.activities, key=lambda item: item.created_at, reverse=True)[
        :limit
    ]
    return [activity_out(item, incident.public_id) for item in activities]


@router.post("/{incident_id}/investigate", response_model=InvestigationOut)
def investigate(
    incident_id: str,
    db: Session = Depends(get_db),
    memory: MemoryService = Depends(get_memory_service),
    llm: GroqLLM = Depends(get_llm_service),
) -> InvestigationOut:
    incident = load_incident(db, incident_id)
    investigation = agent.investigate(db, incident, memory_service=memory, llm=llm)
    return InvestigationOut.model_validate(investigation)


@router.post("/{incident_id}/resolve", response_model=ResolutionOut)
def resolve(
    incident_id: str,
    payload: ResolutionCreate,
    db: Session = Depends(get_db),
) -> ResolutionOut:
    incident = load_incident(db, incident_id)
    resolution = record_resolution(db, incident, payload)
    return ResolutionOut.model_validate(resolution)


@router.post("/{incident_id}/confirm-resolution", response_model=ConfirmResolutionResponse)
def confirm(
    incident_id: str,
    payload: ConfirmResolution,
    db: Session = Depends(get_db),
    memory: MemoryService = Depends(get_memory_service),
) -> ConfirmResolutionResponse:
    incident = load_incident(db, incident_id)
    resolution, retain, retry_only = confirm_resolution(
        db, incident, payload, memory_service=memory
    )
    fresh = load_incident(db, incident.id)
    retention = MemoryRetainResult(
        status=retain.status,
        bank_id=memory.cfg.hindsight_bank_id,
        document_id=retain.document_id,
        retained_at=resolution.retained_at,
        items_count=retain.items_count,
        error=retain.error,
        operation_id=retain.operation_id,
        resolution=ResolutionOut.model_validate(resolution),
    )
    return ConfirmResolutionResponse(
        incident=incident_detail(fresh),
        resolution=ResolutionOut.model_validate(resolution),
        retention=retention,
    )
