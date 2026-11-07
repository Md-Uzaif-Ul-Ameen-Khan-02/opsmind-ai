"""Pydantic schemas: request validation and API response contracts."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Severity = Literal["critical", "high", "medium", "low"]
IncidentStatus = Literal["open", "investigating", "resolved"]
EvidenceSource = Literal[
    "incident_report", "error_log", "deployment_context", "historical_memory", "ai_inference"
]
ActionRisk = Literal["read_only", "low", "requires_approval"]
Likelihood = Literal["high", "medium", "low"]


class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ---------------------------------------------------------------------------
# Incidents
# ---------------------------------------------------------------------------
class IncidentCreate(APIModel):
    title: str = Field(min_length=5, max_length=240)
    description: str = Field(min_length=10, max_length=8000)
    severity: Severity
    environment: str = Field(min_length=2, max_length=40)
    service: str = Field(min_length=2, max_length=120)
    error_logs: str | None = Field(default=None, max_length=20000)
    deployment_info: str | None = Field(default=None, max_length=2000)
    occurred_at: datetime | None = None
    reported_by: str | None = Field(default=None, max_length=120)
    is_synthetic: bool = False

    @field_validator("title", "description", "environment", "service")
    @classmethod
    def _strip_required(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class IncidentUpdate(APIModel):
    title: str | None = Field(default=None, min_length=5, max_length=240)
    description: str | None = Field(default=None, min_length=10, max_length=8000)
    severity: Severity | None = None
    environment: str | None = Field(default=None, min_length=2, max_length=40)
    service: str | None = Field(default=None, min_length=2, max_length=120)
    error_logs: str | None = Field(default=None, max_length=20000)
    deployment_info: str | None = Field(default=None, max_length=2000)
    status: IncidentStatus | None = None


class LinkedIncident(APIModel):
    id: int
    public_id: str
    title: str
    service: str
    status: str
    verified: bool
    resolved_at: datetime | None = None


class HistoricalMemory(APIModel):
    """One memory returned by Hindsight. Content is never synthesised."""

    memory_id: str
    text: str
    type: str | None = None
    context: str | None = None
    score: float | None = None
    document_id: str | None = None
    mentioned_at: str | None = None
    source_chunk: str | None = None
    origin: str = "retrieved_memory"
    linked_incident: LinkedIncident | None = None


class ProbableCause(APIModel):
    cause: str
    likelihood: Likelihood = "medium"
    reasoning: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    category: str | None = None


class EvidenceItem(APIModel):
    observation: str
    source: EvidenceSource = "ai_inference"
    reference: str | None = None
    memory_id: str | None = None


class RecommendedAction(APIModel):
    step: str
    rationale: str = ""
    risk: ActionRisk = "read_only"
    command: str | None = None


class ResolutionCreate(APIModel):
    root_cause: str = Field(min_length=5, max_length=8000)
    resolution_summary: str = Field(min_length=5, max_length=8000)
    actions_taken: str | None = Field(default=None, max_length=8000)
    lessons_learned: str | None = Field(default=None, max_length=4000)
    verification_notes: str | None = Field(default=None, max_length=4000)
    verified_by: str | None = Field(default=None, max_length=120)

    @field_validator("root_cause", "resolution_summary")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class ConfirmResolution(ResolutionCreate):
    """Engineer confirms the verified root cause and fix.

    Confirmation is the only path that writes knowledge into Hindsight.
    """

    verified_by: str = Field(min_length=2, max_length=120)

    @field_validator("verified_by")
    @classmethod
    def _strip_verifier(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("must not be blank")
        return cleaned


class ResolutionOut(APIModel):
    id: int
    incident_id: int
    root_cause: str
    resolution_summary: str
    actions_taken: str | None = None
    lessons_learned: str | None = None
    verification_notes: str | None = None
    verified: bool
    verified_by: str | None = None
    verified_at: datetime | None = None
    retain_status: str
    retain_error: str | None = None
    retained_at: datetime | None = None
    memory_document_id: str | None = None
    memory_bank_id: str | None = None
    created_at: datetime
    updated_at: datetime


class InvestigationOut(APIModel):
    id: int
    incident_id: int
    summary: str
    severity_assessment: Severity
    probable_causes: list[ProbableCause] = Field(default_factory=list)
    supporting_evidence: list[EvidenceItem] = Field(default_factory=list)
    historical_memories: list[HistoricalMemory] = Field(default_factory=list)
    recommended_actions: list[RecommendedAction] = Field(default_factory=list)
    prevention_recommendations: list[str] = Field(default_factory=list)
    memory_assessment: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    requires_human_confirmation: bool = True
    llm_mode: str
    llm_model: str | None = None
    llm_error: str | None = None
    memory_backend: str
    memory_status: str
    memory_query: str | None = None
    memory_error: str | None = None
    memory_count: int = 0
    reflect_text: str | None = None
    reflect_status: str
    reflect_error: str | None = None
    created_at: datetime

    @field_validator(
        "probable_causes",
        "supporting_evidence",
        "historical_memories",
        "recommended_actions",
        "prevention_recommendations",
        mode="before",
    )
    @classmethod
    def _parse_json_lists(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return []
        return value if value is not None else []


class IncidentSummary(APIModel):
    id: int
    public_id: str
    title: str
    severity: Severity
    environment: str
    service: str
    status: IncidentStatus
    occurred_at: datetime
    created_at: datetime
    resolved_at: datetime | None = None
    is_synthetic: bool
    reported_by: str | None = None
    latest_investigation_id: int | None = None
    latest_investigation_confidence: float | None = None
    root_cause: str | None = None
    resolution_summary: str | None = None
    resolution_verified: bool = False
    retain_status: str | None = None
    memory_count: int | None = None


class IncidentDetail(IncidentSummary):
    description: str
    error_logs: str | None = None
    deployment_info: str | None = None
    updated_at: datetime
    resolution: ResolutionOut | None = None
    latest_investigation: InvestigationOut | None = None
    investigation_count: int = 0


class IncidentPage(APIModel):
    items: list[IncidentSummary]
    total: int
    limit: int
    offset: int


# ---------------------------------------------------------------------------
# Dashboard / analytics
# ---------------------------------------------------------------------------
class TrendPoint(APIModel):
    date: str
    incidents: int
    resolved: int


class SeverityCount(APIModel):
    severity: Severity
    count: int


class ServiceCount(APIModel):
    service: str
    count: int


class ActivityOut(APIModel):
    id: int
    incident_id: int | None = None
    operation: str
    status: str
    backend: str
    detail: str
    query: str | None = None
    result_count: int | None = None
    duration_ms: int | None = None
    error: str | None = None
    created_at: datetime
    incident_public_id: str | None = None


class AgentRecommendation(APIModel):
    incident_id: int
    incident_public_id: str
    title: str
    service: str
    severity: Severity
    summary: str
    top_cause: str | None = None
    confidence: float
    memory_count: int
    investigated_at: datetime


class DashboardStats(APIModel):
    total_incidents: int
    open_incidents: int
    investigating_incidents: int
    resolved_incidents: int
    critical_incidents: int
    open_critical_incidents: int
    recurring_incidents: int
    recurring_definition: str
    mean_time_to_resolution_minutes: float | None = None
    mttr_sample_size: int = 0
    severity_distribution: list[SeverityCount]
    trend: list[TrendPoint]
    services_at_risk: list[ServiceCount]
    recent_incidents: list[IncidentSummary]
    recent_activity: list[ActivityOut]
    agent_recommendations: list[AgentRecommendation]
    memory: "MemoryStatusOut"
    verified_resolutions: int
    retained_resolutions: int
    demo_mode: bool


class RootCauseCount(APIModel):
    category: str
    count: int
    services: list[str]
    latest_occurrence: datetime | None = None


class ResolutionPlaybook(APIModel):
    incident_id: int
    incident_public_id: str
    service: str
    root_cause_category: str
    root_cause: str
    resolution_summary: str
    verified_by: str | None = None
    verified_at: datetime | None = None
    retain_status: str


class RecurrenceSeries(APIModel):
    period: str
    total: int
    services: dict[str, int]


class PreventionRecommendation(APIModel):
    recommendation: str
    source_incident_ids: list[str]
    evidence: str


class LearningAnalytics(APIModel):
    total_incidents: int
    resolved_incidents: int
    verified_resolutions: int
    retained_resolutions: int
    recurring_patterns: int
    top_root_causes: list[RootCauseCount]
    services_with_recurrence: list[ServiceCount]
    resolution_playbook: list[ResolutionPlaybook]
    prevention_recommendations: list[PreventionRecommendation]
    recurrence_trend: list[RecurrenceSeries]
    memory: "MemoryStatusOut"
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Memory / settings / health
# ---------------------------------------------------------------------------
class MemoryStatusOut(APIModel):
    provider: str = "Hindsight"
    configured: bool
    connected: bool
    bank_id: str
    endpoint: str
    memory_count: int | None = None
    last_checked_at: datetime | None = None
    error: str | None = None
    detail: str = ""


class MemoryRecallRequest(APIModel):
    query: str = Field(min_length=3, max_length=4000)
    max_tokens: int = Field(default=4096, ge=256, le=16384)

    @field_validator("query")
    @classmethod
    def _strip_query(cls, value: str) -> str:
        return value.strip()


class MemoryRecallResponse(APIModel):
    query: str
    results: list[HistoricalMemory]
    status: str
    memory_backend: str
    bank_id: str
    duration_ms: int
    error: str | None = None
    reflect_text: str | None = None
    reflect_status: str | None = None


class RetainedMemory(APIModel):
    memory_id: str
    text: str | None = None
    type: str | None = None
    mentioned_at: str | None = None
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    document_id: str | None = None
    linked_incident: LinkedIncident | None = None


class MemoryListResponse(APIModel):
    bank_id: str
    items: list[RetainedMemory]
    total: int
    status: str
    error: str | None = None


class MemoryReflectRequest(APIModel):
    query: str = Field(min_length=5, max_length=4000)
    context: str | None = Field(default=None, max_length=2000)


class MemoryReflectResponse(APIModel):
    query: str
    text: str | None = None
    status: str
    error: str | None = None
    duration_ms: int


class MemoryRetainResult(APIModel):
    status: str
    bank_id: str
    document_id: str | None = None
    retained_at: datetime | None = None
    items_count: int | None = None
    error: str | None = None
    operation_id: str | None = None
    resolution: ResolutionOut | None = None
    incident: IncidentDetail | None = None


class ConfigStatus(APIModel):
    llm_configured: bool
    llm_model: str
    llm_provider: str = "Groq"
    hindsight_configured: bool
    hindsight_bank_id: str
    hindsight_endpoint: str
    demo_mode: bool
    memory_provider: str = "Hindsight (Vectorize)"


class HealthResponse(APIModel):
    status: str
    version: str
    environment: str
    database: str
    llm: ConfigStatus
    hindsight: MemoryStatusOut
    demo_mode: bool
    seeded: bool
    timestamp: datetime


class AnalyticsResponse(APIModel):
    learning: LearningAnalytics


class ErrorResponse(APIModel):
    detail: str
    code: str | None = None


class ConfirmResolutionResponse(APIModel):
    incident: IncidentDetail
    resolution: ResolutionOut
    retention: MemoryRetainResult


DashboardStats.model_rebuild()
LearningAnalytics.model_rebuild()
