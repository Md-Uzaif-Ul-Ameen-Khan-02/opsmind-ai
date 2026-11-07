"""Dashboard stats and learning analytics."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_memory_service
from ..schemas import AnalyticsResponse, DashboardStats, LearningAnalytics
from ..services.analytics import dashboard_stats, learning_analytics
from ..services.memory import MemoryService

router = APIRouter(tags=["analytics"])


@router.get("/dashboard/stats", response_model=DashboardStats)
def stats(
    db: Session = Depends(get_db), memory: MemoryService = Depends(get_memory_service)
) -> DashboardStats:
    return dashboard_stats(db, memory)


@router.get("/analytics/learning", response_model=LearningAnalytics)
def learning(
    db: Session = Depends(get_db), memory: MemoryService = Depends(get_memory_service)
) -> LearningAnalytics:
    return learning_analytics(db, memory)
