"""API, persistence and human-in-the-loop behaviour tests."""

from __future__ import annotations

import json

from app.config import settings
from app.models import AgentActivity, Incident, IncidentResolution, Investigation, User
from app.services.taxonomy import classify_text


def _new_incident(client, **overrides):
    payload = {
        "title": "Cache cluster latency after configuration rollout",
        "description": (
            "Latency on the session service increased from 40ms to 2.1s after a config change. "
            "Redis client timeout errors appear in the logs."
        ),
        "severity": "high",
        "environment": "production",
        "service": "session-api",
        "error_logs": "redis.exceptions.TimeoutError: Timeout reading from socket",
        "deployment_info": "session-api 2.1.0 deployed 12 minutes before onset.",
        "reported_by": "Priya Raman",
        "is_synthetic": True,
    }
    payload.update(overrides)
    response = client.post("/api/incidents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ---------------------------------------------------------------------------
# Health / database
# ---------------------------------------------------------------------------
def test_health_reports_real_integration_state(client):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"
    assert body["seeded"] is True
    assert body["hindsight"]["configured"] is False
    assert body["hindsight"]["connected"] is False
    assert body["llm"]["llm_configured"] is False
    assert "memory" in body["hindsight"]["detail"].lower()


def test_database_seed_contents(db):
    seeded_ids = {f"INC-{number}" for number in range(3001, 3014)}
    incidents = db.query(Incident).filter(Incident.public_id.in_(seeded_ids)).all()
    assert len(incidents) == 13
    assert len({incident.public_id for incident in incidents}) == 13
    assert all(incident.is_synthetic for incident in incidents)

    seeded_db_ids = [incident.id for incident in incidents]
    verified = (
        db.query(IncidentResolution)
        .filter(
            IncidentResolution.verified.is_(True),
            IncidentResolution.incident_id.in_(seeded_db_ids),
        )
        .all()
    )
    assert len(verified) == 12
    # No seeded knowledge is marked retained without a real Hindsight call.
    assert all(row.retain_status == "not_retained" for row in verified)
    assert db.query(User).count() == 3
    assert (
        db.query(Investigation)
        .filter(Investigation.incident_id.in_(seeded_db_ids))
        .count()
        == 0
    )


def test_secret_values_are_never_serialized(client, monkeypatch):
    monkeypatch.setattr(settings, "hindsight_api_key", "hs_super_secret_value")
    monkeypatch.setattr(settings, "groq_api_key", "gsk_super_secret_value")
    response = client.get("/api/health?probe=false")
    assert response.status_code == 200
    assert "hs_super_secret_value" not in response.text
    assert "gsk_super_secret_value" not in response.text


# ---------------------------------------------------------------------------
# Incident creation / validation / search
# ---------------------------------------------------------------------------
def test_incident_creation_and_public_id(client, db):
    created = _new_incident(client)
    assert created["public_id"].startswith("INC-")
    assert created["status"] == "open"
    assert created["severity"] == "high"
    assert created["service"] == "session-api"

    stored = db.query(Incident).filter(Incident.public_id == created["public_id"]).one()
    assert stored.title == created["title"]
    assert stored.created_at is not None


def test_incident_validation_rejects_bad_input(client):
    response = client.post("/api/incidents", json={"title": "short"})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"
    assert "required" in response.json()["detail"].lower()

    invalid_severity = client.post(
        "/api/incidents",
        json={
            "title": "Valid title here",
            "description": "Valid description with enough characters.",
            "severity": "urgent",
            "environment": "production",
            "service": "api",
        },
    )
    assert invalid_severity.status_code == 422


def test_incident_lookup_by_public_id_and_filters(client):
    created = _new_incident(client, title="Filterable unique incident title")
    detail = client.get(f"/api/incidents/{created['public_id']}")
    assert detail.status_code == 200
    assert detail.json()["id"] == created["id"]

    missing = client.get("/api/incidents/INC-9999")
    assert missing.status_code == 404

    search = client.get("/api/incidents", params={"q": "Filterable unique"})
    assert search.status_code == 200
    assert any(item["public_id"] == created["public_id"] for item in search.json()["items"])

    filtered = client.get("/api/incidents", params={"severity": "low"})
    assert filtered.status_code == 200
    assert all(item["severity"] == "low" for item in filtered.json()["items"])


# ---------------------------------------------------------------------------
# Investigation + explicit degraded mode
# ---------------------------------------------------------------------------
def test_investigation_fallback_never_fabricates_memory(client):
    listed = client.get("/api/incidents", params={"status": "open"}).json()["items"]
    incident = next(item for item in listed if item["service"] == "payment-api")

    response = client.post(f"/api/incidents/{incident['id']}/investigate")
    assert response.status_code == 200
    body = response.json()

    assert body["memory_status"] == "not_configured"
    assert body["memory_count"] == 0
    assert body["historical_memories"] == []
    assert body["reflect_status"] == "skipped"
    assert body["reflect_text"] is None
    assert body["llm_mode"] == "heuristic_fallback"
    assert body["requires_human_confirmation"] is True
    assert 0 <= body["confidence"] <= 0.5
    assert body["probable_causes"]
    assert all(cause["likelihood"] in {"high", "medium", "low"} for cause in body["probable_causes"])
    assert body["recommended_actions"]
    assert "not configured" in (body["llm_error"] or "").lower()

    detail = client.get(f"/api/incidents/{incident['public_id']}").json()
    assert detail["status"] == "investigating"
    assert detail["latest_investigation"]["id"] == body["id"]


def test_investigation_saved_in_sqlite(client, db):
    created = _new_incident(client, title="Investigation persistence check")
    response = client.post(f"/api/incidents/{created['id']}/investigate")
    assert response.status_code == 200

    stored = db.query(Investigation).filter(Investigation.incident_id == created["id"]).one()
    assert stored.summary
    assert json.loads(stored.historical_memories) == []
    assert stored.memory_status == "not_configured"


# ---------------------------------------------------------------------------
# Resolution → verification → retention
# ---------------------------------------------------------------------------
def test_resolution_is_not_retained_until_verified(client, db):
    created = _new_incident(client, title="Unverified resolution must not retain")

    resolution_payload = {
        "root_cause": "Shared Redis client exhausted maxclients during the rollout.",
        "resolution_summary": "Restored a bounded shared connection pool and redeployed.",
        "lessons_learned": "Cache clients must be pooled and bounded.",
    }
    saved = client.post(f"/api/incidents/{created['id']}/resolve", json=resolution_payload)
    assert saved.status_code == 200
    assert saved.json()["verified"] is False
    assert saved.json()["retain_status"] == "not_retained"

    stored = (
        db.query(IncidentResolution).filter(IncidentResolution.incident_id == created["id"]).one()
    )
    assert stored.verified is False
    assert stored.retained_at is None
    retain_activity = (
        db.query(AgentActivity)
        .filter(
            AgentActivity.incident_id == created["id"], AgentActivity.operation == "retain"
        )
        .count()
    )
    assert retain_activity == 0

    confirmed = client.post(
        f"/api/incidents/{created['id']}/confirm-resolution",
        json={**resolution_payload, "verified_by": "Maya Chen"},
    )
    assert confirmed.status_code == 200
    body = confirmed.json()
    assert body["resolution"]["verified"] is True
    assert body["incident"]["status"] == "resolved"
    # Hindsight is not configured: retention is reported as skipped, never faked.
    assert body["retention"]["status"] == "skipped"
    assert "not configured" in (body["retention"]["error"] or "").lower()
    assert body["resolution"]["memory_document_id"] is None

    retry = client.post(
        f"/api/incidents/{created['id']}/confirm-resolution",
        json={**resolution_payload, "verified_by": "Maya Chen"},
    )
    assert retry.status_code == 200
    assert retry.json()["retention"]["status"] == "skipped"


def test_verified_resolution_is_locked(client):
    listed = client.get("/api/incidents", params={"status": "resolved"}).json()["items"]
    resolved = next(item for item in listed if item["resolution_verified"])
    response = client.post(
        f"/api/incidents/{resolved['id']}/resolve",
        json={
            "root_cause": "Attempting to overwrite confirmed knowledge.",
            "resolution_summary": "This must be rejected.",
        },
    )
    assert response.status_code == 409
    assert "verified" in response.json()["detail"].lower()


def test_confirm_requires_verifier_and_real_fields(client):
    created = _new_incident(client, title="Confirmation validation check")
    response = client.post(
        f"/api/incidents/{created['id']}/confirm-resolution",
        json={"root_cause": "Cause", "resolution_summary": "Fix"},
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Memory endpoints / demo endpoints / analytics
# ---------------------------------------------------------------------------
def test_memory_endpoints_report_unconfigured_without_fabrication(client):
    status = client.get("/api/memory/status").json()
    assert status["configured"] is False
    assert status["connected"] is False
    assert status["memory_count"] is None

    recall = client.post(
        "/api/memory/recall", json={"query": "payment api queuepool timeout"}
    ).json()
    assert recall["status"] == "not_configured"
    assert recall["results"] == []

    reflect = client.post("/api/memory/reflect", json={"query": "What fixed it?"}).json()
    assert reflect["status"] == "skipped"
    assert reflect["text"] is None

    memories = client.get("/api/memory/memories").json()
    assert memories["status"] == "not_configured"
    assert memories["items"] == []

    sync = client.post("/api/memory/sync").json()
    assert sync["status"] == "not_configured"
    assert sync["retained"] == 0


def test_memory_sync_never_marks_retained_when_unconfigured(db):
    rows = db.query(IncidentResolution).filter(IncidentResolution.verified.is_(True)).all()
    assert rows
    assert all(row.retain_status != "retained" for row in rows)


def test_dashboard_stats_are_computed_from_records(client, db):
    total = db.query(Incident).count()
    response = client.get("/api/dashboard/stats")
    assert response.status_code == 200
    body = response.json()

    assert body["total_incidents"] == total
    assert sum(item["count"] for item in body["severity_distribution"]) == total
    assert len(body["trend"]) == 14
    assert body["open_incidents"] + body["investigating_incidents"] + body["resolved_incidents"] == total
    assert body["recurring_definition"]
    assert body["memory"]["configured"] is False
    assert body["retained_resolutions"] == 0
    assert all(recommendation["memory_count"] >= 0 for recommendation in body["agent_recommendations"])


def test_learning_analytics_use_verified_records_only(client, db):
    response = client.get("/api/analytics/learning")
    assert response.status_code == 200
    body = response.json()

    verified_count = (
        db.query(IncidentResolution).filter(IncidentResolution.verified.is_(True)).count()
    )
    assert body["verified_resolutions"] == verified_count
    assert body["retained_resolutions"] == 0
    assert all(entry["incident_public_id"] for entry in body["resolution_playbook"])
    assert all(
        entry["retain_status"] != "retained" for entry in body["resolution_playbook"]
    )
    assert len(body["recurrence_trend"]) == 8
    for note in body["notes"]:
        assert "%" not in note or "percentage" in note.lower()

    # Root-cause grouping must use the verified cause, not the symptoms.
    categories = {item["category"] for item in body["top_root_causes"]}
    assert "Database connection pool exhaustion" in categories


def test_demo_scenario_creation_is_idempotent(client, db):
    catalog = client.get("/api/demo/scenarios").json()
    assert len(catalog["scenarios"]) == 2
    assert all("synthetic" in catalog["note"].lower() for _ in [0])
    assert all(scenario["memory_count"] == 0 for scenario in catalog["scenarios"])

    before = db.query(Incident).count()
    first = client.post("/api/demo/scenario/payment-pool-repeat")
    assert first.status_code == 200
    assert "payment api" in first.json()["title"].lower()
    second = client.post("/api/demo/scenario/payment-pool-repeat")
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert db.query(Incident).count() == before + 1

    unknown = client.post("/api/demo/scenario/not-a-scenario")
    assert unknown.status_code == 404


def test_memory_activity_audit_trail(client):
    rows = client.get("/api/memory/activity").json()
    assert isinstance(rows, list)
    assert all(row["operation"] in {"recall", "retain", "reflect"} for row in rows)


# ---------------------------------------------------------------------------
# Unit-level safety / taxonomy checks
# ---------------------------------------------------------------------------
def test_taxonomy_matches_verified_root_causes():
    assert classify_text("QueuePool limit of size 10 overflow 20 reached") == (
        "Database connection pool exhaustion"
    )
    assert classify_text("Partners shared a rate-limit bucket and received 429s") == (
        "Rate limiting / throttling"
    )
    assert classify_text("deadlock detected; lock-order cycle on stock_levels") == (
        "Database deadlock / lock contention"
    )
    assert classify_text("Log rotation was misconfigured and the disk filled") == (
        "Disk space exhaustion"
    )
