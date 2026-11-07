"""Deterministic, clearly-labelled fallback triage.

This exists only so the product stays usable when no Groq key is configured
or the LLM call fails. It is a rule-based analyzer, not an AI model, and the
UI labels it as such. It never invents memories or claims a fix worked.
"""

from __future__ import annotations

import re

from .taxonomy import classify_text

CATEGORY_KNOWLEDGE: dict[str, dict] = {
    "Database connection pool exhaustion": {
        "causes": [
            (
                "Database connections are leaking or held too long, exhausting the pool",
                "The error signature mentions a pool limit/timeout. Connection leaks, unbounded "
                "sessions, or a new code path acquiring sessions without releasing them commonly "
                "produce this pattern.",
                "high",
            ),
            (
                "Pool sizing or timeout configuration is too small for current concurrency",
                "A traffic increase or a slower database response can consume every pooled "
                "connection even without a leak.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Inspect pool saturation metrics and the count of checked-out connections over the "
                "last hour",
                "Distinguishes a leak (monotonic growth) from a capacity problem (sharp step at a "
                "traffic or deployment change).",
                "read_only",
                None,
            ),
            (
                "Search application logs for pool checkout timeouts and correlate their first "
                "occurrence with the deployment timestamp",
                "If timeouts begin seconds after a rollout, a new code path is the leading suspect.",
                "read_only",
                "kubectl logs deploy/{slug} --since=60m | grep -i \"QueuePool\\|pool\"",
            ),
            (
                "Review the ORM session lifecycle in the request path for missing close/rollback "
                "on exception branches",
                "Leaked sessions are the most common cause of a hard pool ceiling in SQLAlchemy "
                "applications.",
                "read_only",
                None,
            ),
            (
                "Compare configured pool_size/max_overflow/pool_timeout against peak request "
                "concurrency",
                "Read-only configuration review; do not change production settings without a "
                "change request.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Add pool saturation and checkout-wait alerts before exhaustion is user-visible.",
            "Enforce session-per-request context managers with rollback in finally blocks.",
            "Record pool configuration as part of deployment review for services using SQLAlchemy.",
        ],
    },
    "Cache / Redis connectivity": {
        "causes": [
            (
                "Redis client connections are exhausted or churning",
                "Timeout errors from the cache layer usually indicate connection churn, pool "
                "exhaustion, or a network path issue between the service and Redis.",
                "high",
            ),
            (
                "Cache failover or network latency increased between the service and Redis",
                "A failover, DNS change or security-group change can produce timeouts without any "
                "application deployment.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Check Redis client counts, latency and eviction metrics",
                "Read-only info output separates client exhaustion from server-side pressure.",
                "read_only",
                "redis-cli info clients",
            ),
            (
                "Verify DNS resolution and TCP reachability from the service's network",
                "Rules out network/DNS changes before code is suspected.",
                "read_only",
                "kubectl exec deploy/{slug} -- getent hosts redis",
            ),
            (
                "Correlate the first timeout with deployments or infrastructure changes",
                "Establishes whether this is a regression or an environmental change.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Alert on Redis client count and p99 latency before saturation.",
            "Ensure cache clients reuse a bounded connection pool with health checks.",
        ],
    },
    "Memory leak / OOM": {
        "causes": [
            (
                "Application memory growth leading to OOM termination",
                "Heap/RSS growth and OOM events point to retained objects, unbounded caches, or "
                "large payloads accumulating over time.",
                "high",
            ),
            (
                "Container memory limit is lower than the workload's steady-state requirement",
                "A recent limit change or increased payload size can trigger OOM without a code "
                "leak.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Inspect per-pod memory usage and restart counts",
                "Identifies whether a single replica is leaking or all replicas show the same "
                "growth pattern.",
                "read_only",
                "kubectl top pod -l app={slug}",
            ),
            (
                "Review logs for OOMKilled events and heap snapshots around the restart",
                "Confirms the termination reason and the growth phase.",
                "read_only",
                "kubectl logs deploy/{slug} --previous --tail=300",
            ),
            (
                "Look for unbounded in-memory caches, maps or batch buffers on the hot path",
                "Static read-only code review; do not change limits or restart pods to mask the "
                "leak.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Set memory growth alerts relative to steady-state RSS, not just the hard limit.",
            "Bound in-process caches and add TTL/eviction policies.",
        ],
    },
    "Gateway / upstream timeout": {
        "causes": [
            (
                "An upstream dependency is responding slower than the gateway timeout",
                "Gateway timeouts are a symptom; the failing upstream and its latency must be "
                "identified before changing any timeout value.",
                "high",
            ),
            (
                "A deployment changed upstream timeouts, connection reuse or routing",
                "A configuration regression can cause 504s immediately after a rollout.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Identify which upstream produced 504s and compare its p99 latency",
                "Narrows the failure to a dependency rather than the gateway itself.",
                "read_only",
                None,
            ),
            (
                "Inspect gateway access logs for the upstream address and status code mix",
                "Confirms whether failures are timeouts or immediate connection refusals.",
                "read_only",
                "kubectl logs deploy/{slug} --since=30m | grep -i \" 504 \"",
            ),
            (
                "Diff the latest deployment's timeout and retry configuration",
                "Read-only review; changing production timeouts requires a change request.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Alert on upstream p99 latency and gateway 5xx ratio separately.",
            "Cover timeout/retry configuration changes in deployment review.",
        ],
    },
    "Failed deployment / bad release": {
        "causes": [
            (
                "The most recent deployment introduced a regression",
                "Failures beginning during or shortly after a rollout are most often caused by the "
                "new release or its migration.",
                "high",
            ),
            (
                "A schema migration is incompatible with the running application version",
                "Expand/contract violations cause write failures or lock waits during rollout.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Compare the failure start time with the rollout timeline",
                "Establishes correlation; it does not by itself prove the release caused the "
                "failure.",
                "read_only",
                "kubectl rollout history deploy/{slug}",
            ),
            (
                "Check pod status and events for crash loops or failed readiness probes",
                "Separates a bad image from dependency or configuration failures.",
                "read_only",
                "kubectl get pods -l app={slug} -o wide",
            ),
            (
                "Review migration logs for lock waits or failed statements",
                "Read-only log review before considering any rollback decision.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Use expand/contract migrations and gate rollouts on error-rate checks.",
            "Include migration timing in release readiness checks.",
        ],
    },
    "CPU saturation": {
        "causes": [
            (
                "A workload change increased CPU demand beyond available capacity",
                "Expensive queries or unbounded batch work can saturate CPU and inflate latency.",
                "high",
            ),
            (
                "Autoscaling is not reacting to the actual load metric",
                "A scaling policy mismatch can leave replicas capped during a traffic spike.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Inspect per-pod CPU usage and throttling",
                "Confirms whether saturation is real and whether throttling is occurring.",
                "read_only",
                "kubectl top pod -l app={slug}",
            ),
            (
                "Check for slow/expensive queries or batch jobs started near the incident",
                "Identifies the workload that changed rather than assuming capacity is the cause.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Alert on sustained CPU utilization above the autoscaling target.",
            "Add query cost limits and batch-size guards.",
        ],
    },
    "Certificate / TLS expiry": {
        "causes": [
            (
                "A TLS certificate expired or is not being served correctly",
                "Expired or mismatched certificates cause handshake failures that look like "
                "connectivity outages.",
                "high",
            ),
        ],
        "actions": [
            (
                "Inspect the served certificate chain and expiry date",
                "Read-only handshake check against the failing endpoint.",
                "read_only",
                "openssl s_client -connect {slug}:443 -servername {slug}",
            ),
            (
                "Check automated renewal job status and recent failures",
                "Determines whether renewal is broken rather than merely late.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Alert at 21/7/1 days before certificate expiry.",
            "Monitor renewal job success, not only certificate validity.",
        ],
    },
    "Rate limiting / throttling": {
        "causes": [
            (
                "Request volume exceeded a configured rate limit",
                "429 responses indicate a client or traffic shift exceeding quota.",
                "high",
            ),
            (
                "A rate-limit policy change was applied with a limit that is too low for "
                "legitimate traffic",
                "Recent policy changes can throttle valid partners.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Identify which clients and routes receive 429s",
                "Distinguishes abusive traffic from legitimate partner traffic.",
                "read_only",
                "kubectl logs deploy/{slug} --since=30m | grep -i 429",
            ),
            (
                "Review the current rate-limit policy and recent changes",
                "Read-only configuration review before requesting any quota change.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Use tiered limits per client and alert on 429 ratio.",
            "Review quota changes against partner traffic baselines.",
        ],
    },
    "Queue backlog / consumer lag": {
        "causes": [
            (
                "Consumers are failing or too slow, causing the queue to grow",
                "Backlog with consumer errors usually indicates a poison message or a blocked "
                "batch job.",
                "high",
            ),
            (
                "Producer volume increased beyond consumer capacity",
                "A traffic spike or retry storm can outpace consumers even when they are healthy.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Check queue depth, age of oldest message and consumer error rate",
                "Separates a stalled consumer from a volume spike.",
                "read_only",
                None,
            ),
            (
                "Inspect consumer logs for retries and poison-message handling",
                "Read-only log review; do not purge or requeue messages without approval.",
                "read_only",
                "kubectl logs deploy/{slug} --since=45m | grep -i \"retry\\|error\"",
            ),
        ],
        "prevention": [
            "Alert on consumer lag and oldest-message age, not just queue depth.",
            "Add dead-letter handling and idempotent retries.",
        ],
    },
    "Database deadlock / lock contention": {
        "causes": [
            (
                "Transactions acquire row locks in inconsistent order",
                "Deadlocks typically come from competing transactions updating the same rows in "
                "different orders.",
                "high",
            ),
            (
                "Long-running transactions hold locks while waiting on external calls",
                "Lock duration grows with external latency, amplifying contention.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Inspect current lock waits and blocking sessions",
                "Identifies the actual blocking chain before changing application code.",
                "read_only",
                "psql -c \"select pid, wait_event_type, wait_event, query from pg_stat_activity where wait_event_type = 'Lock'\"",
            ),
            (
                "Review transaction order in the affected write paths",
                "A consistent lock ordering is the durable fix.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Enforce a documented lock ordering for multi-row updates.",
            "Keep external calls outside database transactions.",
        ],
    },
    "Disk space exhaustion": {
        "causes": [
            (
                "Log or temporary files filled the volume",
                "Sudden ENOSPC is usually unbounded log growth, missing rotation or temp files.",
                "high",
            ),
            (
                "A data directory grew past the volume's capacity",
                "Retention or cleanup jobs may have stopped running.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Check filesystem and inode usage on the affected nodes",
                "Confirms which mount is full and whether inodes are exhausted.",
                "read_only",
                "df -h",
            ),
            (
                "Find the largest directories under the service's log/temp paths",
                "Identifies the growth source without deleting anything.",
                "read_only",
                "du -sh /var/log/*",
            ),
        ],
        "prevention": [
            "Alert at 80% disk usage and monitor inode usage.",
            "Enforce log rotation and retention policy for high-volume services.",
        ],
    },
    "Environment / configuration error": {
        "causes": [
            (
                "An environment variable or configuration value is incorrect for this environment",
                "Wrong endpoints, credentials or flags commonly surface as connection or "
                "authentication failures.",
                "high",
            ),
            (
                "Configuration was not validated at startup and failed only on a specific code "
                "path",
                "Missing fail-fast validation lets bad configuration reach production traffic.",
                "medium",
            ),
        ],
        "actions": [
            (
                "Compare the effective configuration for this environment against the documented "
                "values (never print secrets)",
                "Read-only review; redact credentials when comparing.",
                "read_only",
                None,
            ),
            (
                "Check deployment events for configuration or secret changes",
                "Correlates the failure window with configuration changes.",
                "read_only",
                "kubectl describe deploy/{slug}",
            ),
        ],
        "prevention": [
            "Add startup validation for required configuration and fail fast.",
            "Keep environment configuration in version control with review.",
        ],
    },
    "Other / unclassified": {
        "causes": [
            (
                "The failure mode is not yet determined from the available evidence",
                "The incident report and logs do not map cleanly to a known pattern. More "
                "diagnostic signal is required.",
                "low",
            ),
        ],
        "actions": [
            (
                "Gather recent events, pod status and the last 200 log lines",
                "Read-only collection to establish the failure mode.",
                "read_only",
                "kubectl get events --sort-by=.lastTimestamp",
            ),
            (
                "Establish the fault timeline relative to deployments and traffic changes",
                "Correlation narrows the search space without changing production.",
                "read_only",
                None,
            ),
        ],
        "prevention": [
            "Improve structured logging and alert coverage for this service.",
        ],
    },
}


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return cleaned[:40] or "service"


def heuristic_investigation(incident, memories: list[dict]) -> dict:
    """Return a schema-compatible investigation produced without an LLM."""
    haystack = " ".join(
        [incident.title, incident.description, incident.error_logs or ""]
    )
    category = classify_text(haystack)
    knowledge = CATEGORY_KNOWLEDGE.get(category, CATEGORY_KNOWLEDGE["Other / unclassified"])
    slug = _slug(incident.service)

    causes = [
        {
            "cause": cause,
            "likelihood": likelihood,
            "reasoning": reasoning,
            "category": category,
            "evidence_refs": ["incident_report", "error_log"],
        }
        for cause, reasoning, likelihood in knowledge["causes"]
    ]

    evidence: list[dict] = [
        {
            "observation": f"Reported symptoms: {incident.description.strip()[:400]}",
            "source": "incident_report",
            "reference": incident.public_id,
            "memory_id": None,
        }
    ]
    if incident.error_logs:
        first_lines = "\n".join(
            line.strip() for line in incident.error_logs.strip().splitlines()[:3]
        )
        evidence.append(
            {
                "observation": f"Provided log excerpt: {first_lines[:600]}",
                "source": "error_log",
                "reference": "error_logs",
                "memory_id": None,
            }
        )
    if incident.deployment_info:
        evidence.append(
            {
                "observation": f"Deployment context: {incident.deployment_info.strip()[:400]}",
                "source": "deployment_context",
                "reference": "deployment_info",
                "memory_id": None,
            }
        )
    for memory in memories[:5]:
        evidence.append(
            {
                "observation": f"Retrieved from Hindsight: {(memory.get('text') or '')[:600]}",
                "source": "historical_memory",
                "reference": memory.get("document_id") or memory.get("memory_id"),
                "memory_id": memory.get("memory_id"),
            }
        )

    actions = [
        {
            "step": step.format(slug=slug),
            "rationale": rationale,
            "risk": risk,
            "command": command.format(slug=slug) if command else None,
        }
        for step, rationale, risk, command in knowledge["actions"]
    ]
    if memories:
        actions.insert(
            0,
            {
                "step": (
                    "Review the retrieved historical resolution(s) and confirm whether the "
                    "current failure mode matches before applying a fix"
                ),
                "rationale": (
                    f"{len(memories)} memory record(s) were retrieved from Hindsight; similarity "
                    "must be assessed by an engineer, not assumed."
                ),
                "risk": "read_only",
                "command": None,
            },
        )

    confidence = 0.28
    if memories:
        confidence += 0.07
    if incident.error_logs:
        confidence += 0.05
    if incident.deployment_info:
        confidence += 0.05
    confidence = min(confidence, 0.5)

    memory_assessment = (
        f"{len(memories)} memory record(s) were retrieved from Hindsight and are attached as "
        "evidence. Rule-based mode does not interpret them; read the memory panel before acting."
        if memories
        else (
            "No relevant memories were retrieved from Hindsight. This rule-based triage is based "
            "only on the incident report, logs and deployment context."
        )
    )

    summary = (
        f"Rule-based triage (deterministic, no LLM): this {incident.environment} incident on "
        f"{incident.service} is most consistent with {category.lower()}. The leading hypothesis "
        f"is: {knowledge['causes'][0][0].lower()}. All causes are hypotheses and require engineer "
        "confirmation."
    )

    return {
        "summary": summary,
        "severity": incident.severity,
        "probable_causes": causes,
        "supporting_evidence": evidence[:10],
        "recommended_actions": actions[:8],
        "prevention_recommendations": _dedupe(knowledge["prevention"] + [
            "Capture the confirmed root cause and fix in OpsMind so Hindsight can reuse it.",
        ]),
        "memory_assessment": memory_assessment,
        "confidence": round(confidence, 3),
    }


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            output.append(value)
    return output
