"""Dashboard and learning analytics computed from real incident records."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session, selectinload

from ..models import AgentActivity, Incident, IncidentResolution
from ..schemas import (
    ActivityOut,
    AgentRecommendation,
    DashboardStats,
    LearningAnalytics,
    MemoryStatusOut,
    PreventionRecommendation,
    RecurrenceSeries,
    ResolutionPlaybook,
    RootCauseCount,
    ServiceCount,
    SeverityCount,
    TrendPoint,
)
from .memory import MemoryService
from .serializers import activity_out, incident_summary, latest_investigation
from .taxonomy import classify_incident, classify_text

SEVERITIES = ("critical", "high", "medium", "low")


def memory_status_out(memory_service: MemoryService, *, probe: bool = True) -> MemoryStatusOut:
    status = memory_service.status(probe=probe)
    return MemoryStatusOut(
        configured=status.configured,
        connected=status.connected,
        bank_id=status.bank_id,
        endpoint=status.endpoint,
        memory_count=status.memory_count,
        last_checked_at=status.last_checked_at,
        error=status.error,
        detail=status.detail,
    )


def _utc_today() -> date:
    return datetime.now(timezone.utc).date()


def _load_incidents(db: Session) -> list[Incident]:
    return (
        db.query(Incident)
        .options(selectinload(Incident.investigations), selectinload(Incident.resolution))
        .order_by(Incident.created_at.desc())
        .all()
    )


def _recent_activity(db: Session, limit: int = 8) -> list[ActivityOut]:
    rows = (
        db.query(AgentActivity)
        .options(selectinload(AgentActivity.incident))
        .order_by(AgentActivity.created_at.desc(), AgentActivity.id.desc())
        .limit(limit)
        .all()
    )
    return [activity_out(row) for row in rows]


def dashboard_stats(db: Session, memory_service: MemoryService) -> DashboardStats:
    incidents = _load_incidents(db)

    total = len(incidents)
    open_count = sum(1 for item in incidents if item.status == "open")
    investigating = sum(1 for item in incidents if item.status == "investigating")
    resolved = sum(1 for item in incidents if item.status == "resolved")
    critical = sum(1 for item in incidents if item.severity == "critical")
    open_critical = sum(
        1 for item in incidents if item.severity == "critical" and item.status != "resolved"
    )

    # Repeat-incident metric: an incident is recurring when its service has
    # more than one recorded incident. Pattern-level recurrence (service +
    # root-cause category) is calculated separately in learning analytics.
    service_totals: dict[str, int] = defaultdict(int)
    for incident in incidents:
        service_totals[incident.service] += 1
    recurring_ids = {
        incident.id
        for incident in incidents
        if service_totals[incident.service] > 1
    }

    durations = [
        (incident.resolved_at - incident.created_at).total_seconds() / 60
        for incident in incidents
        if incident.status == "resolved"
        and incident.resolved_at is not None
        and incident.resolved_at >= incident.created_at
    ]

    severity_distribution = [
        SeverityCount(
            severity=severity,  # type: ignore[arg-type]
            count=sum(1 for item in incidents if item.severity == severity),
        )
        for severity in SEVERITIES
    ]

    today = _utc_today()
    trend: list[TrendPoint] = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        trend.append(
            TrendPoint(
                date=day.isoformat(),
                incidents=sum(1 for item in incidents if item.created_at.date() == day),
                resolved=sum(
                    1
                    for item in incidents
                    if item.resolved_at is not None and item.resolved_at.date() == day
                ),
            )
        )

    service_counts: dict[str, int] = defaultdict(int)
    for incident in incidents:
        service_counts[incident.service] += 1
    services_at_risk = [
        ServiceCount(service=service, count=count)
        for service, count in sorted(service_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if count >= 2
    ][:5]

    verified_resolutions = (
        db.query(IncidentResolution).filter(IncidentResolution.verified.is_(True)).count()
    )
    retained_resolutions = (
        db.query(IncidentResolution)
        .filter(IncidentResolution.retain_status == "retained")
        .count()
    )

    recommendations: list[AgentRecommendation] = []
    for incident in incidents:
        if incident.status == "resolved":
            continue
        investigation = latest_investigation(incident)
        if investigation is None:
            continue
        causes = investigation.probable_causes
        try:
            import json

            parsed = json.loads(causes) if isinstance(causes, str) else causes
        except (ValueError, TypeError):
            parsed = []
        recommendations.append(
            AgentRecommendation(
                incident_id=incident.id,
                incident_public_id=incident.public_id,
                title=incident.title,
                service=incident.service,
                severity=incident.severity,  # type: ignore[arg-type]
                summary=investigation.summary,
                top_cause=parsed[0].get("cause") if parsed else None,
                confidence=investigation.confidence,
                memory_count=investigation.memory_count,
                investigated_at=investigation.created_at,
            )
        )
    severity_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    recommendations.sort(
        key=lambda item: (severity_rank.get(item.severity, 9), -item.investigated_at.timestamp())
    )

    return DashboardStats(
        total_incidents=total,
        open_incidents=open_count,
        investigating_incidents=investigating,
        resolved_incidents=resolved,
        critical_incidents=critical,
        open_critical_incidents=open_critical,
        recurring_incidents=len(recurring_ids),
        recurring_definition=(
            "Incidents on a service with more than one recorded incident."
        ),
        mean_time_to_resolution_minutes=round(sum(durations) / len(durations), 1)
        if durations
        else None,
        mttr_sample_size=len(durations),
        severity_distribution=severity_distribution,
        trend=trend,
        services_at_risk=services_at_risk,
        recent_incidents=[incident_summary(item) for item in incidents[:6]],
        recent_activity=_recent_activity(db, limit=8),
        agent_recommendations=recommendations[:4],
        memory=memory_status_out(memory_service),
        verified_resolutions=verified_resolutions,
        retained_resolutions=retained_resolutions,
        demo_mode=all(item.is_synthetic for item in incidents) if incidents else True,
    )


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def learning_analytics(db: Session, memory_service: MemoryService) -> LearningAnalytics:
    incidents = _load_incidents(db)
    verified = (
        db.query(IncidentResolution)
        .options(selectinload(IncidentResolution.incident))
        .filter(IncidentResolution.verified.is_(True))
        .order_by(IncidentResolution.verified_at.desc())
        .all()
    )

    total = len(incidents)
    resolved = sum(1 for item in incidents if item.status == "resolved")
    retained = sum(1 for item in verified if item.retain_status == "retained")

    category_groups: dict[str, list[IncidentResolution]] = defaultdict(list)
    for resolution in verified:
        category_groups[classify_text(resolution.root_cause)].append(resolution)

    top_root_causes = [
        RootCauseCount(
            category=category,
            count=len(rows),
            services=sorted({row.incident.service for row in rows if row.incident}),
            latest_occurrence=max(
                (row.verified_at for row in rows if row.verified_at), default=None
            ),
        )
        for category, rows in sorted(
            category_groups.items(), key=lambda kv: (-len(kv[1]), kv[0])
        )[:6]
    ]

    grouping: dict[tuple[str, str], list[Incident]] = defaultdict(list)
    for incident in incidents:
        grouping[(incident.service, classify_incident(incident))].append(incident)
    recurring_patterns = sum(1 for rows in grouping.values() if len(rows) > 1)

    service_counts: dict[str, int] = defaultdict(int)
    for incident in incidents:
        service_counts[incident.service] += 1
    services_with_recurrence = [
        ServiceCount(service=service, count=count)
        for service, count in sorted(service_counts.items(), key=lambda kv: (-kv[1], kv[0]))
        if count > 1
    ][:8]

    playbook = [
        ResolutionPlaybook(
            incident_id=resolution.incident_id,
            incident_public_id=resolution.incident.public_id if resolution.incident else "",
            service=resolution.incident.service if resolution.incident else "",
            root_cause_category=classify_text(resolution.root_cause),
            root_cause=resolution.root_cause,
            resolution_summary=resolution.resolution_summary,
            verified_by=resolution.verified_by,
            verified_at=resolution.verified_at,
            retain_status=resolution.retain_status,
        )
        for resolution in verified
        if resolution.incident is not None
    ][:12]

    prevention_map: dict[str, PreventionRecommendation] = {}
    for resolution in verified:
        lesson = (resolution.lessons_learned or "").strip()
        if not lesson or not resolution.incident:
            continue
        key = lesson.lower()
        if key in prevention_map:
            existing = prevention_map[key]
            if resolution.incident.public_id not in existing.source_incident_ids:
                existing.source_incident_ids.append(resolution.incident.public_id)
            continue
        prevention_map[key] = PreventionRecommendation(
            recommendation=lesson[:600],
            source_incident_ids=[resolution.incident.public_id],
            evidence=(
                f"Engineer-authored lesson on verified resolution for "
                f"{resolution.incident.service} "
                f"({classify_text(resolution.root_cause)})."
            ),
        )
    prevention = list(prevention_map.values())[:8]

    today = _utc_today()
    this_week = _week_start(today)
    weeks: list[RecurrenceSeries] = []
    for offset in range(7, -1, -1):
        start = this_week - timedelta(weeks=offset)
        end = start + timedelta(days=7)
        rows = [item for item in incidents if start <= item.created_at.date() < end]
        per_service: dict[str, int] = defaultdict(int)
        for item in rows:
            per_service[item.service] += 1
        weeks.append(
            RecurrenceSeries(
                period=start.isoformat(),
                total=len(rows),
                services=dict(sorted(per_service.items(), key=lambda kv: (-kv[1], kv[0]))),
            )
        )

    notes = [
        "All metrics are computed from OpsMind's incident records; no improvement percentages "
        "are extrapolated.",
        "Mean time to resolution uses report creation → engineer-verified resolution.",
        "Recurring patterns group incidents by service + verified root-cause category.",
        "Prevention items quote engineer-authored lessons learned on verified resolutions.",
    ]
    if not verified:
        notes.append(
            "No verified resolutions yet. Confirm an incident resolution to populate learning "
            "analytics and retain knowledge in Hindsight."
        )

    return LearningAnalytics(
        total_incidents=total,
        resolved_incidents=resolved,
        verified_resolutions=len(verified),
        retained_resolutions=retained,
        recurring_patterns=recurring_patterns,
        top_root_causes=top_root_causes,
        services_with_recurrence=services_with_recurrence,
        resolution_playbook=playbook,
        prevention_recommendations=prevention,
        recurrence_trend=weeks,
        memory=memory_status_out(memory_service),
        notes=notes,
    )
