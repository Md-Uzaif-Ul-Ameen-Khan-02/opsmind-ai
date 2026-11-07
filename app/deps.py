"""Shared service instances and request helpers."""

from __future__ import annotations

from sqlalchemy.orm import Session, selectinload

from .models import Incident
from .services.errors import NotFoundError
from .services.llm import GroqLLM
from .services.memory import MemoryService

memory_service = MemoryService()
llm_service = GroqLLM()


def get_memory_service() -> MemoryService:
    return memory_service


def get_llm_service() -> GroqLLM:
    return llm_service


def load_incident(db: Session, reference: str | int) -> Incident:
    query = db.query(Incident).options(
        selectinload(Incident.investigations),
        selectinload(Incident.resolution),
        selectinload(Incident.activities),
    )
    text = str(reference).strip()
    if text.isdigit():
        incident = query.filter(Incident.id == int(text)).one_or_none()
    else:
        incident = query.filter(Incident.public_id == text.upper()).one_or_none()
    if incident is None:
        raise NotFoundError(f"Incident '{reference}' was not found.")
    return incident
