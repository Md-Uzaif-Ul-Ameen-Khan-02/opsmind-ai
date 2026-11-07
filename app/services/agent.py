"""Incident investigation orchestration.

Flow:
  incident → Hindsight recall → Hindsight reflect → Groq reasoning
           → normalise / validate → persist investigation

Historical memories are built exclusively from the real recall response,
never from model output. Every failure mode is reported explicitly.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from ..config import settings as default_settings
from ..models import Incident, Investigation
from ..schemas import (
    EvidenceItem,
    HistoricalMemory,
    ProbableCause,
    RecommendedAction,
)
from .activity import record_activity
from .heuristics import heuristic_investigation
from .llm import GroqLLM, LLMError, InvestigationDraft
from .memory import MemoryService
from .memory_links import enrich_memories
from .safety import sanitize_action
from .taxonomy import classify_text

logger = logging.getLogger(__name__)

VALID_LIKELIHOOD = {"high", "medium", "low"}
VALID_SEVERITY = {"critical", "high", "medium", "low"}
VALID_EVIDENCE_SOURCES = {
    "incident_report",
    "error_log",
    "deployment_context",
    "historical_memory",
    "ai_inference",
}
SOURCE_ALIASES = {
    "logs": "error_log",
    "log": "error_log",
    "incident_logs": "error_log",
    "incident": "incident_report",
    "report": "incident_report",
    "deployment": "deployment_context",
    "memory": "historical_memory",
    "historical": "historical_memory",
    "hypothesis": "ai_inference",
    "inference": "ai_inference",
    "model": "ai_inference",
}
RISK_ALIASES = {
    "read-only": "read_only",
    "readonly": "read_only",
    "safe": "read_only",
    "read": "read_only",
    "medium": "low",
    "high": "requires_approval",
    "requires-approval": "requires_approval",
    "approval": "requires_approval",
}

LOG_SIGNAL = re.compile(
    r"error|exception|timeout|timed out|refused|failed|fatal|panic|oom|"
    r"queuepool|pool|deadlock|denied|expired|429|500|502|503|504|reset",
    re.IGNORECASE,
)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _salient_log_lines(logs: str | None, limit: int = 8) -> str:
    if not logs:
        return ""
    lines = [line.strip() for line in logs.splitlines() if line.strip()]
    important = [line for line in lines if LOG_SIGNAL.search(line)]
    chosen = important[:limit] or lines[:limit]
    return "\n".join(line[:260] for line in chosen)[:1400]


def build_memory_query(incident: Incident) -> str:
    """Build the recall query from the real failure signature."""
    symptoms = re.sub(r"\s+", " ", incident.description).strip()[:700]
    log_signature = _salient_log_lines(incident.error_logs)
    parts = [
        "Find confirmed historical incident resolutions with the same failure mode.",
        f"Service: {incident.service}",
        f"Environment: {incident.environment}",
        f"Symptoms: {symptoms}",
    ]
    if log_signature:
        parts.append(f"Error signature:\n{log_signature}")
    if incident.deployment_info:
        parts.append(f"Deployment context: {incident.deployment_info.strip()[:400]}")
    parts.append(
        "I need prior incidents with the same symptoms, their confirmed root cause, "
        "the fix that actually worked, and how it was verified."
    )
    return "\n".join(parts)[:2200]


def _normalise_likelihood(value: str | None) -> str:
    lowered = (value or "").strip().lower()
    return lowered if lowered in VALID_LIKELIHOOD else "medium"


def _normalise_severity(value: str | None, default: str) -> str:
    lowered = (value or "").strip().lower()
    return lowered if lowered in VALID_SEVERITY else default


def _normalise_source(value: str | None) -> str:
    lowered = (value or "").strip().lower()
    if lowered in VALID_EVIDENCE_SOURCES:
        return lowered
    return SOURCE_ALIASES.get(lowered, "ai_inference")


def _normalise_risk(value: str | None) -> str:
    lowered = (value or "").strip().lower()
    if lowered in {"read_only", "low", "requires_approval"}:
        return lowered
    return RISK_ALIASES.get(lowered, "read_only")


def _unique_strings(values: list[str], limit: int = 8) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = re.sub(r"\s+", " ", str(value)).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(cleaned[:500])
        if len(result) >= limit:
            break
    return result


def normalise_draft(
    draft: InvestigationDraft,
    *,
    incident: Incident,
    memories: list[HistoricalMemory],
) -> dict:
    """Validate model output against real evidence before persisting it."""
    memory_ids = {memory.memory_id for memory in memories}

    causes: list[ProbableCause] = []
    for cause in draft.probable_causes:
        refs = [ref for ref in cause.evidence_refs if ref and (ref in memory_ids or len(ref) < 200)]
        causes.append(
            ProbableCause(
                cause=cause.cause.strip()[:600],
                likelihood=_normalise_likelihood(cause.likelihood),
                reasoning=cause.reasoning.strip()[:2000],
                evidence_refs=refs[:6],
                category=(cause.category or classify_text(f"{cause.cause} {cause.reasoning}"))[:120],
            )
        )
    if not causes:
        causes = [
            ProbableCause(
                cause="Undetermined failure mode",
                likelihood="low",
                reasoning="The investigation did not return a supported hypothesis.",
                evidence_refs=[],
                category="Other / unclassified",
            )
        ]

    evidence: list[EvidenceItem] = []
    for item in draft.supporting_evidence:
        source = _normalise_source(item.source)
        memory_id = item.memory_id if item.memory_id in memory_ids else None
        observation = item.observation.strip()[:1500]
        if source == "historical_memory" and not memory_id:
            # Never let the model attribute its own claim to memory.
            source = "ai_inference"
            observation = (
                f"{observation} [Attribution corrected: no retrieved memory matches this claim.]"
            )
        evidence.append(
            EvidenceItem(
                observation=observation,
                source=source,
                reference=(item.reference or "")[:300] or None,
                memory_id=memory_id,
            )
        )

    actions: list[RecommendedAction] = []
    for item in draft.recommended_actions:
        actions.append(
            sanitize_action(
                RecommendedAction(
                    step=item.step.strip()[:600],
                    rationale=item.rationale.strip()[:1500],
                    risk=_normalise_risk(item.risk),
                    command=item.command,
                )
            )
        )

    confidence = max(0.0, min(1.0, float(draft.confidence)))
    memory_assessment = (draft.memory_assessment or "").strip()[:2000]
    if not memory_assessment:
        if memories:
            memory_assessment = (
                f"{len(memories)} historical memory record(s) were retrieved from Hindsight "
                "and supplied to the investigation."
            )
        else:
            memory_assessment = (
                "No relevant memories were retrieved from Hindsight. This investigation is "
                "based only on the incident report, logs and deployment context."
            )

    return {
        "summary": draft.summary.strip()[:4000],
        "severity": _normalise_severity(draft.severity, incident.severity),
        "probable_causes": [item.model_dump() for item in causes],
        "supporting_evidence": [item.model_dump() for item in evidence][:12],
        "recommended_actions": [item.model_dump() for item in actions][:10],
        "prevention_recommendations": _unique_strings(draft.prevention_recommendations),
        "memory_assessment": memory_assessment,
        "confidence": round(confidence, 3),
        "requires_human_confirmation": True,
    }


def investigate(
    db: Session,
    incident: Incident,
    *,
    memory_service: MemoryService,
    llm: GroqLLM,
) -> Investigation:
    """Run the full investigation pipeline and persist the result."""
    started = _now()
    query = build_memory_query(incident)
    recall = memory_service.recall(query)

    if recall.status == "not_configured":
        recall_activity_status = "skipped"
    elif recall.status == "error":
        recall_activity_status = "error"
    else:
        recall_activity_status = "success"
    record_activity(
        db,
        operation="recall",
        status=recall_activity_status,
        backend="hindsight",
        incident_id=incident.id,
        query=query,
        result_count=len(recall.results),
        duration_ms=recall.duration_ms,
        detail=(
            f"Recall against Hindsight bank {memory_service.cfg.hindsight_bank_id}: "
            f"{len(recall.results)} memory record(s)."
        ),
        error=recall.error,
    )

    memories = enrich_memories(db, recall.results)

    reflect_text: str | None = None
    reflect_status = "skipped"
    reflect_error: str | None = None
    if recall.status == "ok" and memories:
        reflect_query = (
            f"An engineer is responding to a {incident.severity} incident on "
            f"{incident.service} in {incident.environment}: {incident.title}. "
            "Using only confirmed historical incidents in this bank, explain: what happened "
            "previously, why it is relevant to this incident, what actually resolved it, "
            "whether the current incident is sufficiently similar, and which diagnostic "
            "action should be taken next. Be explicit when the memories are not similar "
            "enough, and never claim a fix succeeded unless it was verified."
        )
        reflect = memory_service.reflect(reflect_query, context=query[:1200])
        reflect_text = reflect.text
        reflect_status = reflect.status
        reflect_error = reflect.error
        record_activity(
            db,
            operation="reflect",
            status="success" if reflect.status == "ok" else "error",
            backend="hindsight",
            incident_id=incident.id,
            query=reflect_query,
            result_count=len(memories),
            duration_ms=reflect.duration_ms,
            detail="Reasoned over retrieved engineering knowledge with Hindsight reflect.",
            error=reflect.error,
        )
    else:
        reflect_error = (
            "Reflect skipped: no relevant memories were retrieved, so there was no "
            "engineering knowledge to reason over."
        )
        record_activity(
            db,
            operation="reflect",
            status="skipped",
            backend="hindsight",
            incident_id=incident.id,
            query=None,
            result_count=0,
            detail=reflect_error,
            error=None,
        )

    llm_mode = "llm" if llm.available else "heuristic_fallback"
    llm_error: str | None = None
    llm_model: str | None = None
    if llm.available:
        try:
            draft, llm_model = llm.investigate(
                incident=incident,
                memories=[memory.model_dump(mode="json") for memory in memories],
                reflect_text=reflect_text,
                memory_query=query,
            )
            parsed = normalise_draft(draft, incident=incident, memories=memories)
        except LLMError as exc:
            llm_mode = "heuristic_fallback"
            llm_error = f"LLM investigation unavailable: {exc}"
            logger.warning("Falling back to heuristic investigation: %s", exc)
            parsed = heuristic_investigation(
                incident, [m.model_dump(mode="json") for m in memories]
            )
    else:
        llm_error = (
            "GROQ_API_KEY is not configured, so no LLM produced this analysis. "
            "The result below comes from OpsMind's deterministic heuristic triage."
        )
        parsed = heuristic_investigation(
            incident, [m.model_dump(mode="json") for m in memories]
        )

    investigation = Investigation(
        incident_id=incident.id,
        summary=parsed["summary"],
        severity_assessment=parsed["severity"],
        probable_causes=json.dumps(parsed["probable_causes"]),
        supporting_evidence=json.dumps(parsed["supporting_evidence"]),
        historical_memories=json.dumps([m.model_dump(mode="json") for m in memories]),
        recommended_actions=json.dumps(parsed["recommended_actions"]),
        prevention_recommendations=json.dumps(parsed["prevention_recommendations"]),
        memory_assessment=parsed.get("memory_assessment"),
        confidence=parsed["confidence"],
        requires_human_confirmation=True,
        llm_mode=llm_mode,
        llm_model=llm_model,
        llm_error=llm_error,
        memory_backend="hindsight",
        memory_status=recall.status,
        memory_query=query,
        memory_error=recall.error,
        memory_count=len(memories),
        reflect_text=reflect_text,
        reflect_status=reflect_status,
        reflect_error=reflect_error,
    )
    incident.investigations.append(investigation)
    if incident.status == "open":
        incident.status = "investigating"

    record_activity(
        db,
        operation="investigate",
        status="success",
        backend="groq" if llm_mode == "llm" else "heuristic",
        incident_id=incident.id,
        result_count=len(memories),
        duration_ms=int((_now() - started).total_seconds() * 1000),
        detail=(
            f"Investigation generated via {'Groq ' + (llm_model or '') if llm_mode == 'llm' else 'heuristic fallback'}; "
            f"{len(memories)} Hindsight memory record(s) used as evidence."
        ),
        error=llm_error,
    )
    db.commit()
    db.refresh(investigation)
    return investigation


def investigation_context(incident: Incident) -> dict:
    """Small helper for logging/UI headers."""
    latest = incident.investigations[-1] if incident.investigations else None
    return {
        "incident": incident.public_id,
        "investigation_id": latest.id if latest else None,
        "llm_mode": latest.llm_mode if latest else None,
        "memory_count": latest.memory_count if latest else 0,
    }
