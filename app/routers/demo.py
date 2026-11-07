"""Repeatable hackathon demonstration controls.

These endpoints only create clearly-labelled synthetic incidents. They never
fabricate investigation results or memory retrievals.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, selectinload

from ..database import get_db
from ..deps import get_memory_service, load_incident
from ..models import Incident
from ..schemas import IncidentDetail
from ..services.demo_data import DEMO_SCENARIO_ONE, DEMO_SCENARIO_TWO
from ..services.memory import MemoryService
from ..services.seeding import (
    get_or_create_scenario,
    scenario_payloads,
    seed_database,
    sync_verified_knowledge,
)
from ..services.serializers import incident_detail

router = APIRouter(prefix="/demo", tags=["demo"])

VALID_KEYS = {DEMO_SCENARIO_ONE["key"], DEMO_SCENARIO_TWO["key"]}


@router.get("/scenarios")
def scenarios(db: Session = Depends(get_db)) -> dict:
    catalog = []
    for item in scenario_payloads():
        existing = (
            db.query(Incident)
            .options(selectinload(Incident.investigations))
            .filter(Incident.title == item["title"])
            .one_or_none()
        )
        latest = (
            max(existing.investigations, key=lambda investigation: investigation.id)
            if existing and existing.investigations
            else None
        )
        catalog.append(
            {
                **item,
                "incident_id": existing.id if existing else None,
                "incident_public_id": existing.public_id if existing else None,
                "status": existing.status if existing else None,
                "memory_count": latest.memory_count if latest else 0,
                "memory_status": latest.memory_status if latest else "not_investigated",
                "llm_mode": latest.llm_mode if latest else None,
            }
        )
    return {
        "scenarios": catalog,
        "demo_company": "Lumen Commerce (fictional)",
        "note": "All scenarios are synthetic demonstration data, not a real environment.",
    }


@router.post("/seed")
def seed(
    reset: bool = Query(default=False, description="Delete local incident records and reseed"),
    sync_memory: bool = Query(default=True, description="Retain verified knowledge in Hindsight"),
    db: Session = Depends(get_db),
    memory: MemoryService = Depends(get_memory_service),
) -> dict:
    result = seed_database(db, reset=reset)
    memory_result = None
    if sync_memory:
        memory_result = sync_verified_knowledge(db, memory)
    return {
        **result,
        "memory_sync": memory_result,
        "note": (
            "Local reset does not delete memories already stored in Hindsight. Use the Hindsight "
            "console to delete the bank if you need a completely clean memory."
        )
        if reset
        else "Synthetic demonstration data.",
    }


@router.post("/scenario/{key}", response_model=IncidentDetail)
def create_scenario(
    key: str,
    db: Session = Depends(get_db),
) -> IncidentDetail:
    if key not in VALID_KEYS:
        from ..services.errors import NotFoundError

        raise NotFoundError(
            f"Unknown demo scenario '{key}'. Available: {', '.join(sorted(VALID_KEYS))}."
        )
    incident, _created = get_or_create_scenario(db, key)
    return incident_detail(load_incident(db, incident.id))
