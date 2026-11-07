"""Health / integration status."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from ..config import settings
from ..database import get_db
from ..deps import get_memory_service
from ..models import Incident
from ..schemas import ConfigStatus, HealthResponse
from ..services.analytics import memory_status_out
from ..services.memory import MemoryService

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def health(
    probe: bool = Query(default=True, description="Probe Hindsight connectivity"),
    db: Session = Depends(get_db),
    memory: MemoryService = Depends(get_memory_service),
) -> HealthResponse:
    database_status = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        database_status = "unavailable"

    return HealthResponse(
        status="ok" if database_status == "ok" else "degraded",
        version=settings.app_version,
        environment=settings.environment,
        database=database_status,
        llm=ConfigStatus(
            llm_configured=settings.llm_configured,
            llm_model=settings.groq_model,
            hindsight_configured=settings.hindsight_configured,
            hindsight_bank_id=settings.hindsight_bank_id,
            hindsight_endpoint=settings.hindsight_endpoint,
            demo_mode=settings.demo_mode,
        ),
        hindsight=memory_status_out(memory, probe=probe),
        demo_mode=settings.demo_mode,
        seeded=db.query(Incident).count() > 0,
        timestamp=datetime.now(timezone.utc).replace(tzinfo=None),
    )
