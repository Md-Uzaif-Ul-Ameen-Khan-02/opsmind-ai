"""Deterministic root-cause taxonomy derived from incident text.

Used only for grouping real records into patterns. Classification prefers the
engineer-verified root cause (weighted double) over symptoms/logs, and uses
weighted technical phrases so generic words like "timeout" do not win.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..models import Incident, IncidentResolution

# (category, ((phrase, weight), ...)) — order is only a tie-breaker.
PATTERNS: list[tuple[str, tuple[tuple[str, float], ...]]] = [
    (
        "Certificate / TLS expiry",
        (
            ("certificate", 3),
            ("x509", 3),
            ("acme", 3),
            ("ssl", 2),
            ("tls", 2),
            ("notafter", 3),
            ("cert expiry", 3),
            ("renewal job", 2),
        ),
    ),
    (
        "Environment / configuration error",
        (
            ("configmap", 3),
            ("environment variable", 3),
            ("redis_url", 3),
            ("pointing at production", 3),
            ("misconfigur", 2),
            ("wrong environment", 3),
            ("startup guard", 2),
            ("config diff", 2),
            ("configuration", 1),
        ),
    ),
    (
        "Disk space exhaustion",
        (
            ("no space left", 4),
            ("enospc", 4),
            ("disk", 3),
            ("inode", 3),
            ("logrotate", 3),
            ("log rotation", 3),
            ("60gb", 2),
        ),
    ),
    (
        "Database deadlock / lock contention",
        (
            ("deadlock", 4),
            ("lock-order", 4),
            ("lock ordering", 4),
            ("sharelock", 3),
            ("lock wait", 2),
            ("blocking create index", 2),
        ),
    ),
    (
        "Queue backlog / consumer lag",
        (
            ("backlog", 4),
            ("consumer lag", 4),
            ("dead-letter", 3),
            ("visibility timeout", 3),
            ("sqs", 2),
            ("kafka", 2),
            ("queue depth", 3),
            ("smtp", 1),
            ("queue", 1),
        ),
    ),
    (
        "Rate limiting / throttling",
        (
            ("rate-limit", 4),
            ("rate limit", 4),
            ("429", 4),
            ("quota", 2),
            ("retry budget", 3),
            ("shared bucket", 3),
        ),
    ),
    (
        "Memory leak / OOM",
        (
            ("memory leak", 4),
            ("oomkill", 4),
            ("out of memory", 4),
            ("heap", 2),
            ("rss", 2),
            ("dedupe cache", 3),
            ("memory limit", 2),
            ("in-memory", 1),
        ),
    ),
    (
        "Database connection pool exhaustion",
        (
            ("queuepool", 4),
            ("connection pool", 3),
            ("pool_size", 3),
            ("max_overflow", 3),
            ("checked_out", 2),
            ("exhausted the pool", 4),
            ("session", 1),
        ),
    ),
    (
        "Cache / Redis connectivity",
        (
            ("redis.exceptions", 4),
            ("redis", 3),
            ("memcached", 3),
            ("maxclients", 3),
            ("cache connection", 3),
            ("cache client", 2),
            ("cache", 1),
        ),
    ),
    (
        "CPU saturation",
        (
            ("cpu", 4),
            ("full-index scan", 4),
            ("saturat", 3),
            ("load average", 3),
            ("cpu_throttled", 4),
            ("cpu throttling", 3),
            ("wildcard quer", 2),
        ),
    ),
    (
        "Gateway / upstream timeout",
        (
            ("504", 4),
            ("gateway timeout", 4),
            ("upstream read timeout", 4),
            ("upstream timed out", 4),
            ("read timeout", 2),
            ("upstream", 1),
        ),
    ),
    (
        "Failed deployment / bad release",
        (
            ("crashloopbackoff", 4),
            ("migration", 3),
            ("create index", 3),
            ("rollback", 2),
            ("rollout", 2),
            ("deployment", 2),
            ("deployed", 1),
            ("release", 1),
        ),
    ),
]

_DEFAULT = "Other / unclassified"


def classify_text(text: str | None) -> str:
    """Classify raw incident/root-cause text into a stable category."""
    if not text:
        return _DEFAULT
    lowered = text.lower()
    best: tuple[float, int, str] | None = None
    for order, (category, rules) in enumerate(PATTERNS):
        score = 0.0
        for phrase, weight in rules:
            if phrase in lowered:
                score += weight
        if score > 0 and (best is None or score > best[0]):
            best = (score, -order, category)
    if best is None:
        return _DEFAULT
    return best[2]


def classify_incident(incident: Incident) -> str:
    """Group an incident by its verified cause when available."""
    context = " ".join(
        part for part in (incident.title, incident.description, incident.error_logs or "") if part
    )
    resolution = incident.resolution
    if resolution is not None and resolution.root_cause:
        # The verified root cause is the strongest signal: count it twice.
        return classify_text(f"{context} {resolution.root_cause} {resolution.root_cause}")
    return classify_text(context)


def verified_resolution_map(db: Session) -> dict[int, IncidentResolution]:
    rows = db.query(IncidentResolution).filter(IncidentResolution.verified.is_(True)).all()
    return {row.incident_id: row for row in rows}


def recurrence_counts(db: Session) -> dict[str, int]:
    """Count incidents per service (all statuses), used for recurrence metrics."""
    from sqlalchemy import func

    rows = (
        db.query(Incident.service, func.count(Incident.id))
        .group_by(Incident.service)
        .order_by(func.count(Incident.id).desc())
        .all()
    )
    return {service: int(count) for service, count in rows}


def normalise_lesson(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()
