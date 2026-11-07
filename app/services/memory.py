"""Hindsight integration: real retain / recall / reflect operations.

All agent memory lives in Hindsight (Vectorize). This module is a thin,
honest adapter around the official ``hindsight-client`` SDK:

* It never fabricates memories, scores or answers.
* When the service is not configured or unreachable it returns an explicit
  ``not_configured`` / ``error`` status (with the provider error) instead of
  substituting local or simulated memory.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

from hindsight_client import Hindsight
from hindsight_client_api.exceptions import (
    ApiException,
    ForbiddenException,
    NotFoundException,
    UnauthorizedException,
)

from ..config import Settings, settings as default_settings
from .taxonomy import classify_text

logger = logging.getLogger(__name__)


@dataclass
class MemoryStatus:
    configured: bool
    connected: bool
    bank_id: str
    endpoint: str
    memory_count: int | None = None
    last_checked_at: datetime | None = None
    error: str | None = None
    detail: str = ""


@dataclass
class RecallOutcome:
    query: str
    results: list[dict] = field(default_factory=list)
    status: str = "ok"  # ok | empty | not_configured | error
    error: str | None = None
    duration_ms: int = 0


@dataclass
class ReflectOutcome:
    text: str | None = None
    status: str = "skipped"  # ok | skipped | error
    error: str | None = None
    duration_ms: int = 0


@dataclass
class RetainOutcome:
    status: str  # retained | failed | skipped
    document_id: str | None = None
    items_count: int | None = None
    operation_id: str | None = None
    error: str | None = None
    duration_ms: int = 0


class MemoryService:
    """One dedicated memory bank for the engineering incident agent."""

    provider = "Hindsight"

    def __init__(self, cfg: Settings | None = None) -> None:
        self.cfg = cfg or default_settings
        self._lock = threading.Lock()
        self._status_cache: MemoryStatus | None = None
        self._status_checked_monotonic: float = 0.0

    # ------------------------------------------------------------------
    # Client / status
    # ------------------------------------------------------------------
    @property
    def configured(self) -> bool:
        return self.cfg.hindsight_configured

    def _client(self) -> Hindsight:
        return Hindsight(
            base_url=self.cfg.hindsight_endpoint,
            api_key=self.cfg.hindsight_api_key,
            timeout=self.cfg.hindsight_timeout,
            max_attempts=2,
        )

    @staticmethod
    def _close(client: Hindsight | None) -> None:
        """Release the SDK's HTTP resources; never mask the operation result."""
        if client is None:
            return
        try:
            client.close()
        except Exception:  # noqa: BLE001 - cleanup must not fail the operation
            logger.debug("Hindsight client close failed", exc_info=True)

    @staticmethod
    def _describe_error(exc: Exception) -> str:
        if isinstance(exc, UnauthorizedException) or isinstance(exc, ForbiddenException):
            return (
                "Hindsight rejected the API key (401/403). Check HINDSIGHT_API_KEY and the "
                "key's bank scope."
            )
        if isinstance(exc, NotFoundException):
            return "Hindsight could not find the requested bank or resource (404)."
        if isinstance(exc, ApiException):
            status = getattr(exc, "status", None)
            body = (getattr(exc, "body", None) or "").strip().replace("\n", " ")
            detail = body[:240] if body else str(exc.reason or "API error")
            return f"Hindsight API error {status or ''}: {detail}".strip()
        name = type(exc).__name__
        message = str(exc).strip().replace("\n", " ")
        return f"{name}: {message[:400]}" if message else name

    def ensure_bank(self) -> tuple[bool, str | None]:
        """Create the incident bank if it does not exist (idempotent)."""
        if not self.configured:
            return False, "Hindsight is not configured (HINDSIGHT_API_KEY missing)."
        try:
            client = self._client()
            try:
                client.create_bank(
                    bank_id=self.cfg.hindsight_bank_id,
                    name="OpsMind Incident Intelligence",
                    mission=(
                        "You are the long-term engineering memory for an incident response "
                        "agent. Remember verified incident root causes, the exact fixes that "
                        "engineers confirmed, verification outcomes and lessons learned so "
                        "future incidents can be diagnosed faster. Only confirmed facts are "
                        "trustworthy."
                    ),
                    background=(
                        "This bank stores confirmed production incident resolutions for the "
                        "OpsMind AI incident response platform. Memories are retained only "
                        "after an engineer verifies the root cause and the fix."
                    ),
                    enable_text_search=True,
                    enable_temporal_retrieval=True,
                )
                return True, None
            finally:
                self._close(client)
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator verbatim
            message = self._describe_error(exc)
            # 409 / "already exists" is success for an idempotent ensure.
            if any(token in message.lower() for token in ("409", "already exist", "conflict")):
                return True, None
            logger.warning("Hindsight ensure_bank failed: %s", message)
            return False, message

    def status(self, *, probe: bool = True, force: bool = False) -> MemoryStatus:
        """Report configuration and (optionally) verified connectivity.

        ``connected`` is only true after a successful Hindsight API call; a
        configured key alone is never reported as connected.
        """
        if not self.configured:
            return MemoryStatus(
                configured=False,
                connected=False,
                bank_id=self.cfg.hindsight_bank_id,
                endpoint=self.cfg.hindsight_endpoint,
                detail=(
                    "Hindsight is not configured. Set HINDSIGHT_API_KEY, HINDSIGHT_API_URL "
                    "and HINDSIGHT_BANK_ID in .env to enable persistent incident memory."
                ),
            )

        now_mono = time.monotonic()
        with self._lock:
            cached = self._status_cache
            fresh = (now_mono - self._status_checked_monotonic) < 45
        if cached is not None and fresh and not force:
            return cached
        if not probe and cached is not None:
            return cached

        status = MemoryStatus(
            configured=True,
            connected=False,
            bank_id=self.cfg.hindsight_bank_id,
            endpoint=self.cfg.hindsight_endpoint,
        )
        started = time.perf_counter()
        client = None
        try:
            # Keep the status probe fast even if the memory service is unreachable.
            client = Hindsight(
                base_url=self.cfg.hindsight_endpoint,
                api_key=self.cfg.hindsight_api_key,
                timeout=min(self.cfg.hindsight_timeout, 12.0),
                max_attempts=1,
            )
            response = client.list_memories(bank_id=self.cfg.hindsight_bank_id, limit=1, offset=0)
            status.connected = True
            status.memory_count = int(getattr(response, "total", 0) or 0)
            status.detail = "Connected to Hindsight memory bank."
        except Exception as exc:  # noqa: BLE001
            status.error = self._describe_error(exc)
            status.detail = "Configured, but the Hindsight API is not reachable."
            logger.warning("Hindsight status probe failed: %s", status.error)
            logger.debug("Probe finished in %.2fs", time.perf_counter() - started)
        finally:
            self._close(client)
        status.last_checked_at = datetime.now(timezone.utc).replace(tzinfo=None)
        with self._lock:
            self._status_cache = status
            self._status_checked_monotonic = time.monotonic()
        return status

    def _mark_success(self, memory_count: int | None = None) -> None:
        with self._lock:
            count = memory_count
            if count is None and self._status_cache is not None:
                count = self._status_cache.memory_count
            self._status_cache = MemoryStatus(
                configured=True,
                connected=True,
                bank_id=self.cfg.hindsight_bank_id,
                endpoint=self.cfg.hindsight_endpoint,
                memory_count=count,
                last_checked_at=datetime.now(timezone.utc).replace(tzinfo=None),
                detail="Connected to Hindsight memory bank.",
            )
            self._status_checked_monotonic = time.monotonic()

    def _mark_error(self, error: str) -> None:
        with self._lock:
            self._status_cache = MemoryStatus(
                configured=True,
                connected=False,
                bank_id=self.cfg.hindsight_bank_id,
                endpoint=self.cfg.hindsight_endpoint,
                last_checked_at=datetime.now(timezone.utc).replace(tzinfo=None),
                error=error,
                detail="Configured, but the last Hindsight operation failed.",
            )
            self._status_checked_monotonic = time.monotonic()

    # ------------------------------------------------------------------
    # Recall
    # ------------------------------------------------------------------
    def recall(self, query: str, *, max_tokens: int = 4096) -> RecallOutcome:
        started = time.perf_counter()
        outcome = RecallOutcome(query=query)
        if not self.configured:
            outcome.status = "not_configured"
            outcome.error = "Hindsight is not configured."
            outcome.duration_ms = int((time.perf_counter() - started) * 1000)
            return outcome
        try:
            client = self._client()
            try:
                response = client.recall(
                    bank_id=self.cfg.hindsight_bank_id,
                    query=query,
                    max_tokens=max_tokens,
                    budget=self.cfg.hindsight_budget,
                    include_chunks=True,
                    max_chunk_tokens=1200,
                )
                chunks = getattr(response, "chunks", None) or {}
                results: list[dict] = []
                for item in getattr(response, "results", None) or []:
                    scores = getattr(item, "scores", None)
                    chunk_id = getattr(item, "chunk_id", None)
                    chunk_text = None
                    if chunk_id and isinstance(chunks, dict):
                        chunk = chunks.get(chunk_id)
                        chunk_text = getattr(chunk, "text", None) if chunk is not None else None
                    results.append(
                        {
                            "memory_id": str(item.id),
                            "text": item.text,
                            "type": getattr(item, "type", None),
                            "context": getattr(item, "context", None),
                            "score": float(scores.final) if scores is not None else None,
                            "document_id": getattr(item, "document_id", None),
                            "mentioned_at": getattr(item, "mentioned_at", None),
                            "source_chunk": chunk_text[:1600] if chunk_text else None,
                            "metadata": getattr(item, "metadata", None) or {},
                            "tags": getattr(item, "tags", None) or [],
                        }
                    )
                outcome.results = results
                outcome.status = "ok" if results else "empty"
                self._mark_success()
            finally:
                self._close(client)
        except Exception as exc:  # noqa: BLE001
            outcome.status = "error"
            outcome.error = self._describe_error(exc)
            self._mark_error(outcome.error)
            logger.warning("Hindsight recall failed: %s", outcome.error)
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # ------------------------------------------------------------------
    # Reflect
    # ------------------------------------------------------------------
    def reflect(self, query: str, *, context: str | None = None) -> ReflectOutcome:
        started = time.perf_counter()
        outcome = ReflectOutcome()
        if not self.configured:
            outcome.status = "skipped"
            outcome.error = "Hindsight is not configured."
            outcome.duration_ms = int((time.perf_counter() - started) * 1000)
            return outcome
        try:
            client = self._client()
            try:
                response = client.reflect(
                    bank_id=self.cfg.hindsight_bank_id,
                    query=query,
                    budget=self.cfg.hindsight_budget,
                    context=context,
                )
                text = (getattr(response, "text", None) or "").strip()
                if text:
                    outcome.text = text
                    outcome.status = "ok"
                    self._mark_success()
                else:
                    outcome.status = "error"
                    outcome.error = "Hindsight reflect returned an empty answer."
                    self._mark_error(outcome.error)
            finally:
                self._close(client)
        except Exception as exc:  # noqa: BLE001
            outcome.status = "error"
            outcome.error = self._describe_error(exc)
            self._mark_error(outcome.error)
            logger.warning("Hindsight reflect failed: %s", outcome.error)
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # ------------------------------------------------------------------
    # Retain (confirmed knowledge only)
    # ------------------------------------------------------------------
    def retain_resolution(self, incident, resolution, verified_by: str | None) -> RetainOutcome:
        """Retain a verified incident resolution as structured knowledge."""
        started = time.perf_counter()
        outcome = RetainOutcome(status="skipped")
        if not self.configured:
            outcome.error = "Hindsight is not configured; verified knowledge was not retained."
            outcome.duration_ms = int((time.perf_counter() - started) * 1000)
            return outcome

        document_id = f"incident-{incident.public_id}"
        content = build_resolution_document(incident, resolution, verified_by)
        occurred = (
            incident.occurred_at.isoformat() + "Z"
            if incident.occurred_at
            else datetime.now(timezone.utc).isoformat()
        )
        metadata = {
            "incident_id": incident.public_id,
            "service": incident.service,
            "environment": incident.environment,
            "severity": incident.severity,
            "kind": "confirmed_resolution",
            "verified": "true",
            "verified_by": verified_by or "unknown",
            "root_cause_category": classify_text(resolution.root_cause),
            "opsmind_schema": "2",
        }
        try:
            self.ensure_bank()
            client = self._client()
            try:
                response = client.retain(
                    bank_id=self.cfg.hindsight_bank_id,
                    content=content,
                    context=(
                        f"Confirmed resolution for {incident.service} ({incident.environment}) "
                        f"incident {incident.public_id}"
                    ),
                    timestamp=occurred,
                    document_id=document_id,
                    metadata=metadata,
                    tags=[
                        "confirmed_resolution",
                        _slug(incident.service),
                        incident.environment.lower(),
                    ],
                )
                success = bool(getattr(response, "success", True))
                if not success:
                    outcome.status = "failed"
                    outcome.error = "Hindsight retain reported success=false."
                else:
                    outcome.status = "retained"
                    outcome.items_count = int(getattr(response, "items_count", 0) or 0)
                    outcome.operation_id = getattr(response, "operation_id", None)
                    outcome.document_id = document_id
                    self._mark_success()
            finally:
                self._close(client)
        except Exception as exc:  # noqa: BLE001
            outcome.status = "failed"
            outcome.error = self._describe_error(exc)
            self._mark_error(outcome.error)
            logger.warning("Hindsight retain failed: %s", outcome.error)
        outcome.document_id = document_id
        outcome.duration_ms = int((time.perf_counter() - started) * 1000)
        return outcome

    # ------------------------------------------------------------------
    # Browse
    # ------------------------------------------------------------------
    def list_memories(self, *, limit: int = 50, offset: int = 0) -> tuple[str, list[dict], int, str | None]:
        """Return (status, items, total, error) from the real bank."""
        if not self.configured:
            return "not_configured", [], 0, "Hindsight is not configured."
        try:
            client = self._client()
            try:
                response = client.list_memories(
                    bank_id=self.cfg.hindsight_bank_id, limit=limit, offset=offset
                )
                items = []
                for item in getattr(response, "items", None) or []:
                    items.append(
                        {
                            "memory_id": str(item.id),
                            "text": getattr(item, "text", None),
                            "type": getattr(item, "fact_type", None),
                            "mentioned_at": getattr(item, "mentioned_at", None),
                            "tags": getattr(item, "tags", None) or [],
                            "metadata": getattr(item, "metadata", None) or {},
                            "document_id": getattr(item, "document_id", None),
                        }
                    )
                total = int(getattr(response, "total", len(items)) or 0)
                self._mark_success(memory_count=total)
                return "ok", items, total, None
            finally:
                self._close(client)
        except Exception as exc:  # noqa: BLE001
            error = self._describe_error(exc)
            self._mark_error(error)
            logger.warning("Hindsight list_memories failed: %s", error)
            return "error", [], 0, error


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "-" for ch in value.lower()).strip("-")[:60]


def build_resolution_document(incident, resolution, verified_by: str | None) -> str:
    """Structured knowledge retained in Hindsight after engineer verification."""
    occurred = incident.occurred_at.isoformat() + "Z" if incident.occurred_at else "unknown"
    verified_at = (
        resolution.verified_at.isoformat() + "Z" if resolution.verified_at else "unknown"
    )
    lines = [
        "CONFIRMED INCIDENT RESOLUTION (verified by an engineer — not an AI hypothesis)",
        "",
        f"Incident: {incident.public_id}",
        f"Service: {incident.service}",
        f"Environment: {incident.environment}",
        f"Severity: {incident.severity}",
        f"Occurred at: {occurred}",
        f"Deployment context: {incident.deployment_info or 'not recorded'}",
        "",
        "Symptoms:",
        incident.description.strip(),
        "",
        "Error messages:",
        (incident.error_logs or "not recorded").strip(),
        "",
        f"Confirmed root cause: {resolution.root_cause.strip()}",
        f"Root cause category: {classify_text(resolution.root_cause)}",
        f"Resolution that worked: {resolution.resolution_summary.strip()}",
        f"Actions taken: {(resolution.actions_taken or 'not recorded').strip()}",
        f"Verification result: resolved and verified by {verified_by or 'an engineer'} "
        f"at {verified_at}",
        f"Lessons learned: {(resolution.lessons_learned or 'not recorded').strip()}",
        f"Verification notes: {(resolution.verification_notes or 'not recorded').strip()}",
    ]
    return "\n".join(lines)
