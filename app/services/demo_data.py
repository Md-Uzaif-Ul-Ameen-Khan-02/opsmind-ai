"""Synthetic demonstration dataset for the fictional company Lumen Commerce.

EVERY record in this module is fabricated demo data (``is_synthetic=True``).
It exists so OpsMind can be demonstrated without connecting a real monitoring
system. None of it describes a real production environment.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

DEMO_COMPANY = "Lumen Commerce"

DEMO_USERS = [
    {"name": "Maya Chen", "email": "maya.chen@lumen-demo.example", "role": "Staff SRE"},
    {"name": "Daniel Okafor", "email": "daniel.okafor@lumen-demo.example", "role": "Platform Engineer"},
    {"name": "Priya Raman", "email": "priya.raman@lumen-demo.example", "role": "Backend Engineer"},
]

HISTORICAL_INCIDENTS = [
    {
        "title": "Orders API 500s during month-end batch export",
        "description": (
            "Between 14:02 and 14:40 UTC, POST /v2/orders/export returned HTTP 500 for 22% of "
            "requests. The error rate climbed steadily for ~35 minutes and recovered after the "
            "export job was stopped. Request concurrency was otherwise normal at ~40 rps."
        ),
        "severity": "high",
        "environment": "production",
        "service": "orders-api",
        "error_logs": (
            "2026-09-25T14:21:08Z ERROR orders-api request_id=4c9a1 POST /v2/orders/export 500 in 30014ms\n"
            "sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 30.00\n"
            "  File \"/app/orders/export_worker.py\", line 118, in export_chunk\n"
            "    session = SessionLocal()\n"
            "  File \"/app/orders/db.py\", line 84, in get_session\n"
            "    return _pool.connect()\n"
            "2026-09-25T14:21:12Z WARN orders-api pool checked_out=30 wait=29.8s job=monthly-export"
        ),
        "deployment_info": (
            "orders-service 5.8.0 deployed 3 days before the incident. Month-end export job was "
            "enabled at 13:55 UTC, 7 minutes before the first failure."
        ),
        "reported_by": "Maya Chen",
        "days_ago": 41.2,
        "duration_minutes": 52,
        "root_cause": (
            "The export worker created a new SQLAlchemy session for every 500-order chunk and "
            "returned early on validation errors without closing the session. Leaked connections "
            "exhausted the pool (size 10 + overflow 20) within 25 minutes."
        ),
        "resolution_summary": (
            "Reused a single session per export worker with try/finally close, capped export "
            "concurrency at 3, and added pool checkout instrumentation. The export was re-run "
            "successfully and checked-out connections stayed below 8."
        ),
        "actions_taken": (
            "Stopped the export job; drained the pool; patched export_worker.py; re-ran the "
            "export; monitored pool usage for 90 minutes."
        ),
        "lessons_learned": (
            "Long-running batch jobs must use a session context manager and release database "
            "connections on every exit path, including validation failures."
        ),
        "verification_notes": "Export completed with 0 errors; pool peak 8/30 over 90 minutes.",
        "verified_by": "Daniel Okafor",
    },
    {
        "title": "Checkout page failing with Redis connection timeouts",
        "description": (
            "GET /cart returned HTTP 500 for 31% of sessions for 18 minutes. Redis client read "
            "timeouts of 2 seconds. Traffic was 1.4x baseline because of a marketing campaign; "
            "there had been no application deployment for 48 hours."
        ),
        "severity": "critical",
        "environment": "production",
        "service": "checkout-web",
        "error_logs": (
            "2026-09-29T09:26:41Z ERROR checkout-web request_id=7dd21 GET /cart 500 in 2041ms\n"
            "redis.exceptions.TimeoutError: Timeout reading from socket\n"
            "  File \"/app/cart/cache.py\", line 57, in get_cart\n"
            "    client = redis.Redis.from_url(settings.REDIS_URL)\n"
            "redis.exceptions.ConnectionError: max number of clients reached (maxclients=1000)\n"
            "2026-09-29T09:27:03Z WARN checkout-web cart_cache_miss_ratio=0.91"
        ),
        "deployment_info": (
            "No checkout-web deployment in the previous 48 hours. Campaign traffic ramp began "
            "at 09:00 UTC; Redis maxclients (1000) was reached at 09:24 UTC."
        ),
        "reported_by": "Priya Raman",
        "days_ago": 37.1,
        "duration_minutes": 96,
        "root_cause": (
            "An earlier refactor replaced the shared Redis connection pool with a new client per "
            "request in the cart service. Connection churn exhausted Redis maxclients during the "
            "traffic ramp, causing read timeouts."
        ),
        "resolution_summary": (
            "Restored a module-level ConnectionPool (max_connections=100, "
            "health_check_interval=30), removed per-request client construction, and set a 500ms "
            "socket timeout. Redis client count stabilized at 120 and error rate returned to 0%."
        ),
        "actions_taken": (
            "Scaled checkout-web down temporarily to stop churn; reverted cart service to the "
            "shared pool; restarted the Redis client metrics dashboard; monitored for 2 hours."
        ),
        "lessons_learned": (
            "Cache clients must be pooled and shared. Per-request construction hides connection "
            "churn until traffic rises."
        ),
        "verification_notes": "0% 5xx for 2 hours under 1.4x campaign traffic; Redis clients 120/1000.",
        "verified_by": "Priya Raman",
    },
    {
        "title": "Notification worker OOMKilled every ~6 hours",
        "description": (
            "notification-worker pods restarted repeatedly with OOMKilled. RSS grew from 220MB to "
            "1.4GB over 5-6 hours, and email delivery lag reached 25 minutes before each restart."
        ),
        "severity": "high",
        "environment": "production",
        "service": "notification-worker",
        "error_logs": (
            "Container notification-worker exceeded memory limit (1Gi), last state: OOMKilled\n"
            "2026-10-03T03:12:44Z WARN notification-worker dedupe_cache_size=1849201 heap_used=1.31Gi\n"
            "2026-10-03T03:12:45Z INFO notification-worker processed_email_batch batch_size=5000"
        ),
        "deployment_info": (
            "notification-worker 3.2.0 introduced a recipient dedupe cache 9 days before the first "
            "OOM. No other change in the window."
        ),
        "reported_by": "Maya Chen",
        "days_ago": 33.3,
        "duration_minutes": 210,
        "root_cause": (
            "An in-memory dedupe dictionary keyed by recipient + template had no eviction. It grew "
            "with every send until the 1Gi container limit was hit and the pod was OOMKilled."
        ),
        "resolution_summary": (
            "Replaced the dictionary with a bounded TTL cache (max 100k entries, 6h TTL), capped "
            "batch size, and rolled out 3.2.1. RSS stabilized at ~260MB over 24 hours."
        ),
        "actions_taken": (
            "Captured heap profile; identified the dedupe dict as the dominant retainers; shipped "
            "the bounded cache; increased the memory alert sensitivity."
        ),
        "lessons_learned": (
            "In-process caches must have a hard size bound and TTL; alert on memory growth rate, "
            "not only the container limit."
        ),
        "verification_notes": "24h with RSS 250-270MB and no restarts; email lag under 40s.",
        "verified_by": "Maya Chen",
    },
    {
        "title": "API gateway 504s for all checkout routes",
        "description": (
            "All /checkout/* requests returned HTTP 504 for 18 minutes. The checkout-web upstream "
            "was healthy at 120ms p99 and had no errors. Gateway logs show upstream read timeouts "
            "after 3 seconds."
        ),
        "severity": "critical",
        "environment": "production",
        "service": "api-gateway",
        "error_logs": (
            "2026-10-07T16:44:02Z ERROR api-gateway upstream timed out (110: Connection timed out) "
            "while reading response header from upstream\n"
            "  upstream: \"http://checkout-web:8080/checkout/confirm\"\n"
            "  request: \"POST /checkout/confirm HTTP/1.1\" upstream_status=000\n"
            "2026-10-07T16:44:03Z WARN api-gateway route=/checkout/* upstream_read_timeout=3s"
        ),
        "deployment_info": (
            "api-gateway 9.14.0 deployed 25 minutes before onset. Changelog: 'tighten default "
            "upstream timeouts for faster failure feedback'."
        ),
        "reported_by": "Daniel Okafor",
        "days_ago": 29.4,
        "duration_minutes": 44,
        "root_cause": (
            "Gateway 9.14.0 changed the default upstream read timeout from 30s to 3s. Checkout "
            "confirmation legitimately takes 4-6s while waiting for payment authorization, so "
            "every confirmation request timed out."
        ),
        "resolution_summary": (
            "Restored a route-level 30s read timeout for /checkout/*, released 9.14.1 with the "
            "corrected default, and added a config test that pins route timeouts."
        ),
        "actions_taken": (
            "Applied route-level timeout override; verified checkout confirmations succeeded; "
            "released the corrected gateway build."
        ),
        "lessons_learned": (
            "Timeout defaults are behavior changes. They require load-test evidence and route-level "
            "review before release."
        ),
        "verification_notes": "Checkout 504 rate 0% for 60 minutes; p99 confirmation 5.1s.",
        "verified_by": "Daniel Okafor",
    },
    {
        "title": "Staging payment deploy CrashLoopBackOff after migration",
        "description": (
            "payment-api staging pods crash-looped after v2.4.0. Startup failed while connecting to "
            "the database and staging checkout smoke tests failed. Production was unaffected."
        ),
        "severity": "medium",
        "environment": "staging",
        "service": "payment-api",
        "error_logs": (
            "psycopg2.errors.LockNotAvailable: canceling statement due to lock timeout\n"
            "STATEMENT:  CREATE INDEX ix_charges_created_at ON charges (created_at);\n"
            "2026-10-11T11:03:22Z ERROR payment-api startup failed after 120s: database lock timeout\n"
            "kubectl: pod payment-api-7f9c CrashLoopBackOff (back-off 80s restarting failed container)"
        ),
        "deployment_info": (
            "payment-service v2.4.0 migration added a non-concurrent index on charges(created_at) "
            "as part of the rolling deploy."
        ),
        "reported_by": "Priya Raman",
        "days_ago": 25.2,
        "duration_minutes": 38,
        "root_cause": (
            "A blocking CREATE INDEX in the release migration held a lock longer than the startup "
            "timeout while old and new pods were both running, so new pods never became ready."
        ),
        "resolution_summary": (
            "Rolled back v2.4.0, rewrote the migration as CREATE INDEX CONCURRENTLY in a "
            "pre-deploy job, and redeployed v2.4.0-mig2 successfully."
        ),
        "actions_taken": (
            "Rolled back the deployment; cancelled the blocked index build; moved the migration "
            "to a pre-deploy job; verified staging smoke tests."
        ),
        "lessons_learned": (
            "Migrations must be non-blocking and must run outside the rolling deploy window."
        ),
        "verification_notes": "Staging smoke suite green; migration completed in 41s with no lock waits.",
        "verified_by": "Priya Raman",
    },
    {
        "title": "Search API latency spike after wildcard query surge",
        "description": (
            "Search API p99 latency rose from 180ms to 9.2s for 40 minutes. All six replicas ran at "
            "100% CPU and the autoscaler did not add capacity because the configured metric was "
            "request rate, which was flat."
        ),
        "severity": "high",
        "environment": "production",
        "service": "search-api",
        "error_logs": (
            "2026-10-15T12:18:55Z WARN search-api slow_query q=\"*widget*\" filters=category:home "
            "took 7421ms\n"
            "2026-10-15T12:19:02Z WARN search-api cpu_throttled_percent=68 replicas=6\n"
            "2026-10-15T12:19:40Z ERROR search-api request deadline exceeded after 9000ms"
        ),
        "deployment_info": (
            "search-api 7.1.2 deployed 4 days earlier with no configuration change. A partner "
            "started issuing unbounded leading-wildcard queries at 12:05 UTC."
        ),
        "reported_by": "Daniel Okafor",
        "days_ago": 21.5,
        "duration_minutes": 85,
        "root_cause": (
            "Leading-wildcard queries bypassed the prefix cache and forced full-index scans, "
            "saturating CPU. Autoscaling was tied to request rate, which stayed flat."
        ),
        "resolution_summary": (
            "Added a query-cost guard rejecting leading wildcards longer than 3 characters, raised "
            "the replica ceiling to 12, and switched autoscaling to CPU utilization. p99 returned "
            "to 190ms."
        ),
        "actions_taken": (
            "Identified the expensive query shape in slow logs; shipped the query guard; corrected "
            "HPA metrics; notified the partner."
        ),
        "lessons_learned": (
            "Search endpoints need query-cost limits, not only traffic limits, and autoscaling "
            "should follow the resource that actually saturates."
        ),
        "verification_notes": "p99 190ms for 2 hours; CPU at 35%; wildcard queries rejected with 400.",
        "verified_by": "Daniel Okafor",
    },
    {
        "title": "Expired TLS certificate broke SSO login",
        "description": (
            "All SSO logins failed with TLS handshake errors for 12 minutes. Internal clients "
            "reported an expired certificate. The public API and existing sessions were unaffected."
        ),
        "severity": "critical",
        "environment": "production",
        "service": "auth-service",
        "error_logs": (
            "2026-10-19T08:02:17Z ERROR auth-service upstream TLS handshake failed: "
            "x509: certificate has expired or is not yet valid\n"
            "  certificate: CN=id.internal.lumen-demo.example\n"
            "  notAfter=2026-10-19T07:59:00Z\n"
            "2026-10-19T08:02:20Z WARN cert-renewer last_success=2026-09-19T07:58:00Z attempts_since=3"
        ),
        "deployment_info": (
            "No deployment. The ACME renewal job had failed silently for three cycles after a DNS "
            "credential rotation."
        ),
        "reported_by": "Maya Chen",
        "days_ago": 17.2,
        "duration_minutes": 33,
        "root_cause": (
            "The internal ingress certificate expired because the ACME renewal cron job was failing "
            "on a rotated DNS credential, and renewal failures were not alerted."
        ),
        "resolution_summary": (
            "Renewed the certificate manually, fixed the DNS credential secret, and added expiry "
            "alerts at 21/7/1 days plus renewal-job failure alerts."
        ),
        "actions_taken": (
            "Renewed cert; reloaded ingress; repaired the ACME DNS credential; added certificate "
            "and renewal-job monitoring."
        ),
        "lessons_learned": (
            "Monitor certificate expiry and renewal-job success. An expired certificate is a "
            "process failure, not a surprise."
        ),
        "verification_notes": "SSO login success 100% for 24h; renewal job succeeded on schedule.",
        "verified_by": "Maya Chen",
    },
    {
        "title": "Partner integration throttled by shared gateway rate limit",
        "description": (
            "A partner's order sync received HTTP 429 for two hours. Their traffic was within the "
            "contracted 50 rps but shared a global 200 rps bucket with another partner that had "
            "enabled aggressive retries."
        ),
        "severity": "medium",
        "environment": "production",
        "service": "api-gateway",
        "error_logs": (
            "2026-10-23T11:41:09Z WARN api-gateway limiting requests, excess: 8123.221 by zone "
            "\"partner-shared\"\n"
            "2026-10-23T11:41:09Z INFO api-gateway client=partner-sync status=429 route=/v2/orders/sync\n"
            "2026-10-23T11:41:12Z WARN api-gateway retry_burst client=partner-b status=429"
        ),
        "deployment_info": (
            "No deployment. A second partner enabled aggressive retries at 11:00 UTC, consuming "
            "the shared 200 rps quota."
        ),
        "reported_by": "Priya Raman",
        "days_ago": 13.4,
        "duration_minutes": 61,
        "root_cause": (
            "Partners shared a single gateway rate-limit bucket, so one partner's retry storm "
            "exhausted the quota and throttled a well-behaved partner."
        ),
        "resolution_summary": (
            "Split partner quotas into per-client keys (50 rps each), added a per-client retry "
            "budget, and replayed the failed sync. 429 responses stopped immediately."
        ),
        "actions_taken": (
            "Issued a temporary quota override for the affected partner; split limit keys; "
            "coordinated a controlled replay of the failed sync."
        ),
        "lessons_learned": (
            "Rate limits must be scoped per client. Shared buckets turn one client's retry storm "
            "into another client's outage."
        ),
        "verification_notes": "Sync replay completed; 0 429s for 3 hours; both partners within quota.",
        "verified_by": "Priya Raman",
    },
    {
        "title": "Email queue backlog after blocked batch consumer",
        "description": (
            "Email queue depth grew from 2k to 180k in 50 minutes and the oldest message age reached "
            "48 minutes. Consumer CPU was normal but throughput was near zero."
        ),
        "severity": "high",
        "environment": "production",
        "service": "notification-worker",
        "error_logs": (
            "2026-10-27T15:08:31Z WARN notification-worker consumer poll returned 0 messages "
            "handler_blocked=312s\n"
            "  smtp connect: timed out after 300s\n"
            "2026-10-27T15:08:33Z ERROR notification-worker visibility timeout exceeded "
            "message_id=em_88123 retry_count=12\n"
            "2026-10-27T15:09:02Z WARN notification-worker queue_depth=180421 oldest_age_seconds=2880"
        ),
        "deployment_info": (
            "No deployment. The SMTP provider had a partial outage beginning 55 minutes before the "
            "backlog was noticed."
        ),
        "reported_by": "Daniel Okafor",
        "days_ago": 10.3,
        "duration_minutes": 250,
        "root_cause": (
            "The consumer blocked synchronously on SMTP connections with no timeout, holding a "
            "batch until the visibility timeout expired. Poison messages were retried without a "
            "dead-letter path."
        ),
        "resolution_summary": (
            "Set SMTP connect/read timeouts to 5s, reduced prefetch to 20, added exponential "
            "backoff and a DLQ after 5 attempts, then drained the backlog at 1.2k msg/min."
        ),
        "actions_taken": (
            "Paused non-critical notification classes; shipped consumer timeouts and DLQ; scaled "
            "consumers to 8; replayed the backlog."
        ),
        "lessons_learned": (
            "Consumers must bound external calls and have a dead-letter path. Monitor "
            "oldest-message age, not just queue depth."
        ),
        "verification_notes": "Queue drained to <100; oldest age under 30s; DLQ captured 14 messages.",
        "verified_by": "Daniel Okafor",
    },
    {
        "title": "Inventory sync deadlocks during flash-sale reservations",
        "description": (
            "Inventory sync failed with database deadlocks during a flash sale. 1,240 reservations "
            "rolled back over six minutes; retries succeeded after 15 minutes. No stock was "
            "double-reserved."
        ),
        "severity": "high",
        "environment": "production",
        "service": "inventory-sync",
        "error_logs": (
            "2026-10-30T18:22:10Z ERROR inventory-sync reservation_id=rsv_99213 failed\n"
            "psycopg2.errors.DeadlockDetected: deadlock detected\n"
            "DETAIL: Process 4412 waits for ShareLock on transaction 88213; blocked by process 4390.\n"
            "STATEMENT: UPDATE stock_levels SET reserved = reserved + 1 WHERE sku = $1 AND warehouse_id = $2\n"
            "2026-10-30T18:22:12Z WARN inventory-sync retry_attempt=3 reservation_id=rsv_99213"
        ),
        "deployment_info": (
            "inventory-sync 4.6.0 deployed 2 days earlier, adding a multi-warehouse reservation "
            "path next to the legacy single-warehouse path."
        ),
        "reported_by": "Priya Raman",
        "days_ago": 7.2,
        "duration_minutes": 75,
        "root_cause": (
            "The new reservation path updated stock_levels rows in warehouse order while the legacy "
            "path used SKU order, creating a lock-order cycle under concurrent reservations."
        ),
        "resolution_summary": (
            "Sorted all stock updates by (sku, warehouse_id) before writing, moved external calls "
            "outside the transaction, and added deadlock retry with jitter. The flash-sale replay "
            "completed with zero deadlocks."
        ),
        "actions_taken": (
            "Paused new multi-warehouse reservations; patched lock ordering; replayed failed "
            "reservations; verified stock invariants."
        ),
        "lessons_learned": (
            "Multi-row updates need one global lock ordering, and database transactions must not "
            "wrap network calls."
        ),
        "verification_notes": "Stock invariant check clean; 0 deadlocks for 24h including a load test.",
        "verified_by": "Priya Raman",
    },
    {
        "title": "Orders API nodes hit disk pressure and rejected writes",
        "description": (
            "Two orders-api nodes reached 100% disk usage; writes failed with ENOSPC and readiness "
            "probes removed the nodes from rotation. 6% of order writes failed for 12 minutes."
        ),
        "severity": "high",
        "environment": "production",
        "service": "orders-api",
        "error_logs": (
            "2026-11-02T10:14:02Z ERROR orders-api write failed: [Errno 28] No space left on device\n"
            "  path=/var/log/orders-api/app.log\n"
            "2026-11-02T10:14:05Z ERROR orders-api readiness probe failed (disk pressure)\n"
            "node-exporter: /var 84G used, 0 available, inodes 12% used"
        ),
        "deployment_info": (
            "No deployment. Log volume increased 4x after a debug logging flag was enabled during "
            "an earlier investigation and never reverted."
        ),
        "reported_by": "Maya Chen",
        "days_ago": 4.1,
        "duration_minutes": 120,
        "root_cause": (
            "A temporary debug log level left enabled on two nodes produced 60GB of logs, and log "
            "rotation was misconfigured for the new log path."
        ),
        "resolution_summary": (
            "Rotated and removed old log files, reset the debug flag through configuration, fixed "
            "logrotate for the new path, and added disk/inode alerts at 80%."
        ),
        "actions_taken": (
            "Cleared rotated logs; restored nodes to rotation; fixed logrotate; added capacity "
            "alerts."
        ),
        "lessons_learned": (
            "Temporary debug changes need an expiry, and disk/inode alerts must cover application "
            "log paths."
        ),
        "verification_notes": "Disk usage 41%; no write errors for 48h; logrotate ran twice.",
        "verified_by": "Maya Chen",
    },
    {
        "title": "Checkout staging pointed at production Redis",
        "description": (
            "Staging checkout began writing cart sessions into the production Redis instance after "
            "a configuration rollout. Staging tests passed, but production cart keys showed "
            "unexpected traffic from staging."
        ),
        "severity": "medium",
        "environment": "staging",
        "service": "checkout-web",
        "error_logs": (
            "2026-11-04T13:02:11Z INFO checkout-web redis connection established "
            "host=redis-prod.internal:6379 db=0\n"
            "2026-11-04T13:02:11Z INFO checkout-web env=staging config_source=configmap\n"
            "2026-11-04T13:09:44Z WARN cart-audit unexpected key prefix cart:stg: from 10.4.2.19"
        ),
        "deployment_info": (
            "staging ConfigMap updated 20 minutes earlier; REDIS_URL was copied from the production "
            "template and not overridden."
        ),
        "reported_by": "Daniel Okafor",
        "days_ago": 2.05,
        "duration_minutes": 90,
        "root_cause": (
            "A staging ConfigMap was created from the production template and retained REDIS_URL "
            "pointing at production. The application had no startup guard against cross-environment "
            "endpoints."
        ),
        "resolution_summary": (
            "Restored the staging Redis URL, purged the staging keys from production after "
            "verification, added a startup check rejecting production endpoints in staging, and "
            "added a config diff gate in CI."
        ),
        "actions_taken": (
            "Corrected the ConfigMap; deleted only verified staging-prefixed keys; shipped the "
            "startup guard; added CI config diff."
        ),
        "lessons_learned": (
            "Applications must fail fast when configuration points at another environment's "
            "infrastructure."
        ),
        "verification_notes": "Staging writes only to staging Redis; production cart keys unchanged for 48h.",
        "verified_by": "Daniel Okafor",
    },
]

DEMO_SCENARIO_ONE = {
    "key": "payment-pool-new",
    "title": "Payment API returning HTTP 500 after v2.4.1 deployment",
    "description": (
        "Ten minutes after payment-service v2.4.1 was rolled out, charge requests to "
        "POST /v1/charges began failing with HTTP 500. About 38% of charge attempts fail and "
        "successful charges show elevated latency (p99 12.4s). No infrastructure changes were "
        "reported. Checkout is losing payment attempts right now."
    ),
    "severity": "critical",
    "environment": "production",
    "service": "payment-api",
    "error_logs": (
        "{ts} ERROR payment-api request_id=8f2c1 POST /v1/charges 500 in 30012ms\n"
        "sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 30.00\n"
        "  File \"/app/payment/db.py\", line 84, in get_session\n"
        "    return _pool.connect()\n"
        "  File \"/app/payment/reconcile.py\", line 212, in reconcile_batch\n"
        "    session = SessionLocal()\n"
        "{ts} WARN payment-api pool checkout wait 29.8s checked_out=30 checked_in=0"
    ),
    "deployment_info": (
        "payment-service v2.4.1 deployed ~10 minutes before the first failure (rolling, 100% "
        "shortly after). Changes: new bulk charge reconciliation job (#4821) and retry "
        "middleware (#4830). Previous version v2.4.0 had been stable for 11 days."
    ),
    "reported_by": "Maya Chen",
    "minutes_ago": 25,
    "is_synthetic": True,
}

DEMO_SCENARIO_TWO = {
    "key": "payment-pool-repeat",
    "title": "Payment API charge timeouts recurring after v2.4.3 rollout",
    "description": (
        "Payment API is returning intermittent HTTP 500s and 30-second timeouts on POST /v1/charges "
        "in production. p99 latency is 28s and 14% of charge attempts fail. Logs show the same "
        "SQLAlchemy QueuePool timeout. payment-service v2.4.3 was rolled out earlier today, which "
        "re-enabled the bulk reconciliation job with additional throughput."
    ),
    "severity": "critical",
    "environment": "production",
    "service": "payment-api",
    "error_logs": (
        "{ts} ERROR payment-api request_id=b31f9 POST /v1/charges 500 in 30008ms\n"
        "sqlalchemy.exc.TimeoutError: QueuePool limit of size 10 overflow 20 reached, connection timed out, timeout 30.00\n"
        "  File \"/app/payment/db.py\", line 84, in get_session\n"
        "  File \"/app/payment/reconcile.py\", line 233, in reconcile_partition\n"
        "{ts} WARN payment-api pool checkout wait 29.9s checked_out=30 success_rate=0.86\n"
        "{ts} WARN payment-api reconcile_partition workers=16 batch=2000"
    ),
    "deployment_info": (
        "payment-service v2.4.3 deployed earlier today. The release re-enabled the bulk "
        "reconciliation job with 16 workers and batch size 2000 (the previous incident was "
        "remediated with concurrency 4)."
    ),
    "reported_by": "Priya Raman",
    "minutes_ago": 0,
    "is_synthetic": True,
}


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def build_historical_seed(now: datetime | None = None) -> list[dict]:
    """Return fully-formed seed records with timestamps and resolutions."""
    reference = now or utc_now()
    records: list[dict] = []
    for item in HISTORICAL_INCIDENTS:
        occurred_at = reference - timedelta(days=item["days_ago"])
        resolved_at = occurred_at + timedelta(minutes=item["duration_minutes"])
        records.append(
            {
                **{k: v for k, v in item.items() if k not in {"days_ago", "duration_minutes"}},
                "occurred_at": occurred_at,
                "created_at": occurred_at + timedelta(minutes=2),
                "resolved_at": resolved_at,
                "status": "resolved",
                "is_synthetic": True,
                "resolution": {
                    "root_cause": item["root_cause"],
                    "resolution_summary": item["resolution_summary"],
                    "actions_taken": item["actions_taken"],
                    "lessons_learned": item["lessons_learned"],
                    "verification_notes": item["verification_notes"],
                    "verified_by": item["verified_by"],
                    "verified_at": resolved_at,
                },
            }
        )
    return records


def build_scenario_incident(key: str, now: datetime | None = None) -> dict:
    source = {"payment-pool-new": DEMO_SCENARIO_ONE, "payment-pool-repeat": DEMO_SCENARIO_TWO}[key]
    reference = now or utc_now()
    occurred_at = reference - timedelta(minutes=source["minutes_ago"])
    stamp = occurred_at.isoformat() + "Z"
    return {
        "title": source["title"],
        "description": source["description"],
        "severity": source["severity"],
        "environment": source["environment"],
        "service": source["service"],
        "error_logs": source["error_logs"].replace("{ts}", stamp),
        "deployment_info": source["deployment_info"],
        "reported_by": source["reported_by"],
        "occurred_at": occurred_at,
        "created_at": occurred_at + timedelta(minutes=1),
        "status": "open",
        "is_synthetic": bool(source.get("is_synthetic", True)),
        "scenario_key": key,
    }


def scenario_catalog() -> list[dict]:
    return [
        {
            "key": DEMO_SCENARIO_ONE["key"],
            "act": 1,
            "title": DEMO_SCENARIO_ONE["title"],
            "service": DEMO_SCENARIO_ONE["service"],
            "environment": DEMO_SCENARIO_ONE["environment"],
            "severity": DEMO_SCENARIO_ONE["severity"],
            "summary": (
                "A payment API pool timeout appears 10 minutes after a deployment. Investigate it, "
                "record the engineer-verified root cause and fix, and let OpsMind retain it."
            ),
        },
        {
            "key": DEMO_SCENARIO_TWO["key"],
            "act": 2,
            "title": DEMO_SCENARIO_TWO["title"],
            "service": DEMO_SCENARIO_TWO["service"],
            "environment": DEMO_SCENARIO_TWO["environment"],
            "severity": DEMO_SCENARIO_TWO["severity"],
            "summary": (
                "A similar payment API timeout appears later. Hindsight should surface the "
                "confirmed resolution from Act 1 and make the recommendations more specific."
            ),
        },
    ]
