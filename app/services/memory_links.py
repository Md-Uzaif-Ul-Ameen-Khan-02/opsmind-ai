"""Map Hindsight memories back to the incident records they came from.

This uses only real linkage: the ``document_id`` / metadata that OpsMind
wrote during a verified retain, matched against SQLite incident records.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from ..models import Incident
from ..schemas import HistoricalMemory, LinkedIncident, RetainedMemory


def _public_id_from_document(document_id: str | None) -> str | None:
    if not document_id:
        return None
    prefix = "incident-"
    if document_id.startswith(prefix):
        return document_id[len(prefix) :]
    return None


def enrich_memories(db: Session, raw_results: list[dict]) -> list[HistoricalMemory]:
    public_ids = {
        pid
        for pid in (_public_id_from_document(item.get("document_id")) for item in raw_results)
        if pid
    }
    incidents: dict[str, Incident] = {}
    if public_ids:
        rows = db.query(Incident).filter(Incident.public_id.in_(public_ids)).all()
        incidents = {row.public_id: row for row in rows}

    enriched: list[HistoricalMemory] = []
    for item in raw_results:
        metadata = item.get("metadata") or {}
        public_id = _public_id_from_document(item.get("document_id"))
        incident = incidents.get(public_id) if public_id else None
        verified = bool(incident and incident.resolution and incident.resolution.verified)
        kind = str(metadata.get("kind", ""))
        if kind == "confirmed_resolution" or str(metadata.get("verified", "")).lower() == "true":
            origin = "confirmed_resolution"
        elif verified:
            origin = "verified_incident_source"
        else:
            origin = "retrieved_memory"

        linked = None
        if incident is not None:
            linked = LinkedIncident(
                id=incident.id,
                public_id=incident.public_id,
                title=incident.title,
                service=incident.service,
                status=incident.status,
                verified=verified,
                resolved_at=incident.resolved_at,
            )

        score = item.get("score")
        enriched.append(
            HistoricalMemory(
                memory_id=str(item.get("memory_id") or ""),
                text=item.get("text") or "",
                type=item.get("type"),
                context=item.get("context"),
                score=round(float(score), 4) if score is not None else None,
                document_id=item.get("document_id"),
                mentioned_at=item.get("mentioned_at"),
                source_chunk=item.get("source_chunk"),
                origin=origin,
                linked_incident=linked,
            )
        )
    return enriched


def enrich_retained(db: Session, items: list[dict]) -> list[RetainedMemory]:
    """Attach OpsMind incident linkage to raw Hindsight list_memories items."""
    public_ids = {
        pid
        for pid in (_public_id_from_document(item.get("document_id")) for item in items)
        if pid
    }
    incidents: dict[str, Incident] = {}
    if public_ids:
        rows = db.query(Incident).filter(Incident.public_id.in_(public_ids)).all()
        incidents = {row.public_id: row for row in rows}

    results: list[RetainedMemory] = []
    for item in items:
        public_id = _public_id_from_document(item.get("document_id"))
        incident = incidents.get(public_id) if public_id else None
        linked = None
        if incident is not None:
            linked = LinkedIncident(
                id=incident.id,
                public_id=incident.public_id,
                title=incident.title,
                service=incident.service,
                status=incident.status,
                verified=bool(incident.resolution and incident.resolution.verified),
                resolved_at=incident.resolved_at,
            )
        results.append(
            RetainedMemory(
                memory_id=str(item.get("memory_id") or ""),
                text=item.get("text"),
                type=item.get("type"),
                mentioned_at=item.get("mentioned_at"),
                tags=item.get("tags") or [],
                metadata=item.get("metadata") or {},
                document_id=item.get("document_id"),
                linked_incident=linked,
            )
        )
    return results
