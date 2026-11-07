"""Relational models for incident records.

Hindsight is the system of record for *agent memory*. SQLite holds the
structured operational records (incidents, investigations, resolutions and
an audit trail of agent activity) and is deliberately NOT a vector store.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now() -> datetime:
    """Naive UTC timestamp (stored as UTC, rendered as UTC by the client)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    email: Mapped[str] = mapped_column(String(180), unique=True, nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(60), nullable=False, default="Engineer")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(primary_key=True)
    public_id: Mapped[str] = mapped_column(String(24), unique=True, nullable=False, index=True)

    title: Mapped[str] = mapped_column(String(240), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    environment: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    service: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    error_logs: Mapped[str | None] = mapped_column(Text, nullable=True)
    deployment_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    reported_by: Mapped[str | None] = mapped_column(String(120), nullable=True)

    status: Mapped[str] = mapped_column(String(24), nullable=False, default="open", index=True)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    occurred_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    investigations: Mapped[list[Investigation]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="Investigation.created_at",
    )
    resolution: Mapped[IncidentResolution | None] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        uselist=False,
    )
    activities: Mapped[list[AgentActivity]] = relationship(
        back_populates="incident",
        cascade="all, delete-orphan",
        order_by="AgentActivity.created_at",
    )

    __table_args__ = (
        Index("ix_incidents_service_status", "service", "status"),
        Index("ix_incidents_severity_created", "severity", "created_at"),
    )


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    summary: Mapped[str] = mapped_column(Text, nullable=False)
    severity_assessment: Mapped[str] = mapped_column(String(16), nullable=False)
    probable_causes: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    supporting_evidence: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    historical_memories: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    recommended_actions: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    prevention_recommendations: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    memory_assessment: Mapped[str | None] = mapped_column(Text, nullable=True)

    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    requires_human_confirmation: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Provenance / honesty fields -------------------------------------
    llm_mode: Mapped[str] = mapped_column(String(32), nullable=False, default="llm")
    llm_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    llm_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_backend: Mapped[str] = mapped_column(String(32), nullable=False, default="hindsight")
    memory_status: Mapped[str] = mapped_column(String(32), nullable=False, default="ok")
    memory_query: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    memory_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reflect_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reflect_status: Mapped[str] = mapped_column(String(32), nullable=False, default="skipped")
    reflect_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, index=True)

    incident: Mapped[Incident] = relationship(back_populates="investigations")

    __table_args__ = (Index("ix_investigations_incident_created", "incident_id", "created_at"),)


class IncidentResolution(Base):
    __tablename__ = "incident_resolutions"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), unique=True, nullable=False
    )

    root_cause: Mapped[str] = mapped_column(Text, nullable=False)
    resolution_summary: Mapped[str] = mapped_column(Text, nullable=False)
    actions_taken: Mapped[str | None] = mapped_column(Text, nullable=True)
    lessons_learned: Mapped[str | None] = mapped_column(Text, nullable=True)
    verification_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    verified_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    retain_status: Mapped[str] = mapped_column(String(24), nullable=False, default="not_retained")
    retain_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    retained_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    memory_document_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    memory_bank_id: Mapped[str | None] = mapped_column(String(120), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    incident: Mapped[Incident] = relationship(back_populates="resolution")


class AgentActivity(Base):
    __tablename__ = "agent_activity"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_id: Mapped[int | None] = mapped_column(
        ForeignKey("incidents.id", ondelete="CASCADE"), nullable=True, index=True
    )

    operation: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="success")
    backend: Mapped[str] = mapped_column(String(32), nullable=False, default="hindsight")
    detail: Mapped[str] = mapped_column(Text, nullable=False, default="")
    query: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utc_now, index=True)

    incident: Mapped[Incident | None] = relationship(back_populates="activities")

    __table_args__ = (Index("ix_agent_activity_operation_created", "operation", "created_at"),)
