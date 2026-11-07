"""Agent validation, safety guardrails and missing-memory handling."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.deps import get_memory_service
from app.main import app
from app.models import Incident, IncidentResolution
from app.schemas import HistoricalMemory
from app.services.agent import normalise_draft
from app.services.llm import DraftAction, DraftCause, DraftEvidence, InvestigationDraft
from app.services.memory import RecallOutcome, ReflectOutcome
from app.services.memory import build_resolution_document
from app.services.safety import command_is_safe, sanitize_action
from app.schemas import RecommendedAction


def _incident() -> Incident:
    return Incident(
        id=1,
        public_id="INC-9001",
        title="Payment API 500s",
        description="Charges fail with a pool timeout after a deployment.",
        severity="critical",
        environment="production",
        service="payment-api",
        error_logs="QueuePool limit reached",
        deployment_info="v2.4.1 deployed 10 minutes earlier",
        occurred_at=datetime(2026, 11, 6, 9, 40),
    )


def _memory(memory_id: str = "mem-1") -> HistoricalMemory:
    return HistoricalMemory(
        memory_id=memory_id,
        text="Confirmed pool leak resolution.",
        origin="confirmed_resolution",
        score=0.9,
    )


def _draft(**overrides) -> InvestigationDraft:
    data = {
        "summary": "A verified causal summary of the incident under investigation.",
        "severity": "critical",
        "probable_causes": [
            DraftCause(
                cause="Connection pool exhaustion from a session leak",
                likelihood="high",
                reasoning="Pool timeouts began minutes after the deployment.",
                evidence_refs=["mem-1"],
                category="Database connection pool exhaustion",
            )
        ],
        "supporting_evidence": [
            DraftEvidence(
                observation="Pool metrics grew monotonically after the release.",
                source="historical_memory",
                memory_id="mem-1",
            ),
            DraftEvidence(
                observation="The model believes sessions leaked.",
                source="historical_memory",
                memory_id="mem-does-not-exist",
            ),
        ],
        "recommended_actions": [
            DraftAction(
                step="Inspect pool checkout metrics",
                rationale="Distinguishes a leak from a capacity problem.",
                risk="read_only",
                command="kubectl logs deploy/payment-api --tail=100",
            ),
            DraftAction(
                step="Restart payment pods",
                rationale="Rolling restart",
                risk="low",
                command="kubectl delete pod payment-api-abc",
            ),
        ],
        "prevention_recommendations": ["Add pool saturation alerts.", "Add pool saturation alerts."],
        "confidence": 0.72,
        "requires_human_confirmation": False,
        "memory_assessment": "One confirmed resolution matches the failure signature.",
    }
    data.update(overrides)
    return InvestigationDraft.model_validate(data)


def test_normalise_draft_rejects_fabricated_memory_and_unsafe_commands():
    draft = _draft()
    result = normalise_draft(draft, incident=_incident(), memories=[_memory()])

    memory_evidence = [
        item for item in result["supporting_evidence"] if item["source"] == "historical_memory"
    ]
    assert len(memory_evidence) == 1
    assert memory_evidence[0]["memory_id"] == "mem-1"

    fabricated = next(
        item
        for item in result["supporting_evidence"]
        if "model believes" in item["observation"]
    )
    assert fabricated["source"] == "ai_inference"
    assert fabricated["memory_id"] is None
    assert "no retrieved memory matches" in fabricated["observation"].lower()

    safe_action = result["recommended_actions"][0]
    assert safe_action["command"].startswith("kubectl logs")
    assert safe_action["risk"] in {"read_only", "low"}

    blocked_action = result["recommended_actions"][1]
    assert blocked_action["command"] is None
    assert blocked_action["risk"] == "requires_approval"
    assert "withheld" in blocked_action["rationale"].lower()

    assert result["requires_human_confirmation"] is True
    assert result["prevention_recommendations"] == ["Add pool saturation alerts."]


def test_normalise_draft_handles_empty_memory_honestly():
    draft = _draft(
        supporting_evidence=[],
        memory_assessment=None,
        probable_causes=[
            DraftCause(cause="Undetermined failure", likelihood="unknown", reasoning="")
        ],
    )
    result = normalise_draft(draft, incident=_incident(), memories=[])
    assert "no relevant memories" in result["memory_assessment"].lower()
    assert result["probable_causes"][0]["likelihood"] == "medium"


def test_safety_guardrail_unit_cases():
    assert command_is_safe("kubectl logs deploy/payment-api --tail=200")
    assert command_is_safe("df -h")
    assert command_is_safe("openssl s_client -connect example:443")
    assert not command_is_safe("kubectl delete pod api-1")
    assert not command_is_safe("rm -rf /var/lib/postgresql")
    assert not command_is_safe("sudo systemctl restart payment-api")
    assert not command_is_safe("psql -c \"drop table charges\"")

    unsafe = sanitize_action(
        RecommendedAction(
            step="Restart the service",
            rationale="Apply the fix",
            risk="low",
            command="systemctl restart payment-api",
        )
    )
    assert unsafe.command is None
    assert unsafe.risk == "requires_approval"


def test_resolution_document_contains_verified_provenance():
    incident = _incident()
    resolution = IncidentResolution(
        incident_id=1,
        root_cause="A session leak exhausted the pool.",
        resolution_summary="Bounded the session lifecycle.",
        actions_taken="Rolled back and patched.",
        lessons_learned="Always close sessions.",
        verification_notes="Zero 500s for 45 minutes.",
        verified=True,
        verified_by="Maya Chen",
        verified_at=datetime(2026, 11, 6, 10, 0),
    )
    document = build_resolution_document(incident, resolution, "Maya Chen")
    assert "CONFIRMED INCIDENT RESOLUTION" in document
    assert "INC-9001" in document
    assert "payment-api" in document
    assert "A session leak exhausted the pool." in document
    assert "Zero 500s for 45 minutes." in document
    assert "verified by Maya Chen" in document


class EmptyMemoryService:
    cfg = SimpleNamespace(hindsight_bank_id="empty-test-bank")

    def recall(self, query: str, **_kwargs) -> RecallOutcome:
        return RecallOutcome(query=query, results=[], status="empty", duration_ms=3)

    def reflect(self, *_args, **_kwargs) -> ReflectOutcome:  # pragma: no cover - must not run
        raise AssertionError("Reflect must not run when recall returned no memories.")

    def retain_resolution(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Retain must not run during investigation.")


class ErrorMemoryService:
    cfg = SimpleNamespace(hindsight_bank_id="error-test-bank")

    def recall(self, query: str, **_kwargs) -> RecallOutcome:
        return RecallOutcome(
            query=query,
            results=[],
            status="error",
            error="Hindsight API error 500: provider unavailable",
            duration_ms=5,
        )

    def reflect(self, *_args, **_kwargs) -> ReflectOutcome:  # pragma: no cover
        raise AssertionError("Reflect must not run after a recall error.")

    def retain_resolution(self, *_args, **_kwargs):  # pragma: no cover
        raise AssertionError("Retain must not run during investigation.")


def _create_incident(client: TestClient) -> dict:
    response = client.post(
        "/api/incidents",
        json={
            "title": "Memory handling scenario",
            "description": "A test incident for empty memory handling paths.",
            "severity": "high",
            "environment": "staging",
            "service": "test-service",
            "is_synthetic": True,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_investigation_handles_empty_memory_without_reflect(client):
    app.dependency_overrides[get_memory_service] = lambda: EmptyMemoryService()
    try:
        incident = _create_incident(client)
        response = client.post(f"/api/incidents/{incident['id']}/investigate")
        assert response.status_code == 200
        body = response.json()
        assert body["memory_status"] == "empty"
        assert body["historical_memories"] == []
        assert body["memory_count"] == 0
        assert body["reflect_status"] == "skipped"
        assert body["reflect_text"] is None
    finally:
        app.dependency_overrides.clear()


def test_investigation_handles_memory_provider_failure(client):
    app.dependency_overrides[get_memory_service] = lambda: ErrorMemoryService()
    try:
        incident = _create_incident(client)
        response = client.post(f"/api/incidents/{incident['id']}/investigate")
        assert response.status_code == 200
        body = response.json()
        assert body["memory_status"] == "error"
        assert body["memory_count"] == 0
        assert body["historical_memories"] == []
        assert "provider unavailable" in (body["memory_error"] or "")
        assert body["reflect_status"] == "skipped"
    finally:
        app.dependency_overrides.clear()
