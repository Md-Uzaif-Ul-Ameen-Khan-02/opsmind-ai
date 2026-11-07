"""Wire-level tests for the real Hindsight SDK adapter.

A local HTTP stub speaks the Hindsight REST shape and records requests. These
tests verify URL construction, auth headers, request payloads, SDK response
parsing and error handling. The stub is a test double only: the application
never uses it and never simulates memory.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from app.config import Settings
from app.models import Incident, IncidentResolution
from app.services.memory import MemoryService

RECALL_TEXT = "Confirmed resolution for INC-2001: leaked SQLAlchemy sessions exhausted the pool."
CHUNK_TEXT = (
    "CONFIRMED INCIDENT RESOLUTION (verified by an engineer)\nIncident: INC-2001\n"
    "Root cause: session leak\nResolution: bounded session usage."
)


class StubState:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.recall_results: list[dict] = [
            {
                "id": "mem-1",
                "text": RECALL_TEXT,
                "type": "world",
                "context": "payment-api incident memory",
                "document_id": "incident-INC-2001",
                "metadata": {"kind": "confirmed_resolution", "verified": "true"},
                "chunk_id": "chunk-1",
                "scores": {"final": 0.87},
                "mentioned_at": "2026-01-02T10:00:00Z",
            }
        ]
        self.fail_recall = False
        self.list_total = 1


STATE = StubState()


class StubHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # silence test output
        return

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        try:
            return json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {"_raw": raw}

    def _send(self, payload: dict, status: int = 200) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_PUT(self):  # noqa: N802
        body = self._body()
        STATE.calls.append(
            {
                "method": "PUT",
                "path": self.path,
                "body": body,
                "auth": self.headers.get("Authorization"),
            }
        )
        self._send(
            {
                "bank_id": "opsmind-test",
                "name": "OpsMind Incident Intelligence",
                "mission": "Remember verified incident resolutions.",
                "disposition": {"skepticism": 3, "literalism": 3, "empathy": 3},
            }
        )

    def do_GET(self):  # noqa: N802
        STATE.calls.append(
            {
                "method": "GET",
                "path": self.path,
                "body": {},
                "auth": self.headers.get("Authorization"),
            }
        )
        self._send(
            {
                "items": [
                    {
                        "id": "mem-1",
                        "text": RECALL_TEXT,
                        "fact_type": "world",
                        "mentioned_at": "2026-01-02T10:00:00Z",
                        "tags": ["confirmed_resolution"],
                        "metadata": {"incident_id": "INC-2001"},
                        "document_id": "incident-INC-2001",
                    }
                ],
                "total": STATE.list_total,
                "limit": 50,
                "offset": 0,
            }
        )

    def do_POST(self):  # noqa: N802
        body = self._body()
        STATE.calls.append(
            {
                "method": "POST",
                "path": self.path,
                "body": body,
                "auth": self.headers.get("Authorization"),
            }
        )
        if self.path.endswith("/memories/recall"):
            if STATE.fail_recall:
                self._send({"detail": "internal error"}, status=500)
                return
            self._send(
                {
                    "results": STATE.recall_results,
                    "chunks": {
                        "chunk-1": {"id": "chunk-1", "text": CHUNK_TEXT, "chunk_index": 0}
                    },
                }
            )
            return
        if self.path.endswith("/reflect"):
            self._send({"text": "The previous incident is relevant because the pool signature matches."})
            return
        if self.path.endswith("/memories"):
            self._send(
                {
                    "success": True,
                    "bank_id": "opsmind-test",
                    "items_count": 3,
                    "async": False,
                    "operation_id": "op-123",
                }
            )
            return
        self._send({}, status=404)


@pytest.fixture(scope="module")
def stub_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    STATE.calls.clear()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)


@pytest.fixture()
def memory(stub_url):
    cfg = Settings(
        hindsight_api_url=stub_url,
        hindsight_api_key="test-hindsight-key",
        hindsight_bank_id="opsmind-test",
        hindsight_timeout=8,
        groq_api_key=None,
    )
    return MemoryService(cfg)


def _incident() -> Incident:
    return Incident(
        id=77,
        public_id="INC-7777",
        title="Payment API returning HTTP 500 after rollout",
        description="Charge requests fail with HTTP 500 after deployment.",
        severity="critical",
        environment="production",
        service="payment-api",
        error_logs="sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20 reached",
        deployment_info="payment-service v2.4.1 deployed 10 minutes before onset.",
        status="investigating",
        occurred_at=datetime(2026, 11, 6, 9, 40),
        created_at=datetime(2026, 11, 6, 9, 42),
    )


def _resolution() -> IncidentResolution:
    return IncidentResolution(
        incident_id=77,
        root_cause=(
            "The reconciliation job leaked SQLAlchemy sessions on exception paths until the "
            "connection pool was exhausted."
        ),
        resolution_summary=(
            "Wrapped reconciliation in a session context manager, capped concurrency and "
            "increased the pool size."
        ),
        actions_taken="Rolled back the release and redeployed a patched build.",
        lessons_learned="Batch jobs must close sessions on every exit path.",
        verified=True,
        verified_by="Maya Chen",
        verified_at=datetime(2026, 11, 6, 10, 16),
        retain_status="pending",
    )


def test_retain_writes_verified_knowledge_through_the_sdk(memory):
    STATE.calls.clear()
    ok, error = memory.ensure_bank()
    assert ok is True and error is None
    bank_call = next(call for call in STATE.calls if call["method"] == "PUT")
    assert bank_call["path"].endswith("/v1/default/banks/opsmind-test")
    assert bank_call["auth"] == "Bearer test-hindsight-key"

    outcome = memory.retain_resolution(_incident(), _resolution(), "Maya Chen")
    assert outcome.status == "retained"
    assert outcome.document_id == "incident-INC-7777"
    assert outcome.items_count == 3

    retain_call = next(
        call for call in STATE.calls if call["method"] == "POST" and call["path"].endswith("/memories")
    )
    item = retain_call["body"]["items"][0]
    assert "CONFIRMED INCIDENT RESOLUTION" in item["content"]
    assert "INC-7777" in item["content"]
    assert "Root cause category:" in item["content"]
    assert "The reconciliation job leaked SQLAlchemy sessions" in item["content"]
    assert item["metadata"]["incident_id"] == "INC-7777"
    assert item["metadata"]["kind"] == "confirmed_resolution"
    assert item["metadata"]["verified"] == "true"
    assert item["document_id"] == "incident-INC-7777"
    assert "confirmed_resolution" in item["tags"]
    assert retain_call["auth"] == "Bearer test-hindsight-key"


def test_recall_parses_sdk_response_and_source_chunks(memory):
    STATE.calls.clear()
    outcome = memory.recall("payment api queuepool timeout")
    assert outcome.status == "ok"
    assert len(outcome.results) == 1
    result = outcome.results[0]
    assert result["memory_id"] == "mem-1"
    assert result["text"] == RECALL_TEXT
    assert result["score"] == pytest.approx(0.87)
    assert result["source_chunk"] == CHUNK_TEXT
    assert result["document_id"] == "incident-INC-2001"
    assert result["metadata"]["kind"] == "confirmed_resolution"

    recall_call = next(call for call in STATE.calls if call["path"].endswith("/memories/recall"))
    assert recall_call["body"]["query"] == "payment api queuepool timeout"


def test_reflect_and_list_use_real_endpoints(memory):
    STATE.calls.clear()
    reflection = memory.reflect("What fixed the previous incident?")
    assert reflection.status == "ok"
    assert "pool signature matches" in (reflection.text or "")

    status_value, items, total, error = memory.list_memories(limit=10)
    assert (status_value, total, error) == ("ok", 1, None)
    assert items[0]["memory_id"] == "mem-1"
    assert items[0]["metadata"]["incident_id"] == "INC-2001"

    paths = [call["path"] for call in STATE.calls]
    assert any(path.endswith("/reflect") for path in paths)
    assert any("/memories/list" in path for path in paths)


def test_empty_recall_is_reported_as_empty(memory):
    STATE.recall_results = []
    try:
        outcome = memory.recall("nothing should match this")
        assert outcome.status == "empty"
        assert outcome.results == []
        assert outcome.error is None
    finally:
        STATE.recall_results = [
            {
                "id": "mem-1",
                "text": RECALL_TEXT,
                "type": "world",
                "document_id": "incident-INC-2001",
            }
        ]


def test_provider_error_is_surfaced_without_fabrication(memory):
    STATE.fail_recall = True
    try:
        outcome = memory.recall("payment api queuepool timeout")
        assert outcome.status == "error"
        assert outcome.results == []
        assert outcome.error
        assert "500" in outcome.error or "Hindsight" in outcome.error
    finally:
        STATE.fail_recall = False


def test_connection_failure_is_surfaced():
    cfg = Settings(
        hindsight_api_url="http://127.0.0.1:1",
        hindsight_api_key="test-key",
        hindsight_bank_id="unreachable-bank",
        hindsight_timeout=2,
        groq_api_key=None,
    )
    service = MemoryService(cfg)
    outcome = service.recall("any query")
    assert outcome.status == "error"
    assert outcome.results == []
    assert outcome.error

    status = service.status(probe=True, force=True)
    assert status.configured is True
    assert status.connected is False
    assert status.error


def test_unconfigured_service_never_calls_network():
    service = MemoryService(
        Settings(
            hindsight_api_url="http://127.0.0.1:1",
            hindsight_api_key=None,
            hindsight_bank_id="empty-bank",
            groq_api_key=None,
        )
    )
    assert service.configured is False
    assert service.recall("anything").status == "not_configured"
    assert service.reflect("anything").status == "skipped"
    retain = service.retain_resolution(_incident(), _resolution(), "Maya Chen")
    assert retain.status == "skipped"
    assert "not configured" in (retain.error or "").lower()
