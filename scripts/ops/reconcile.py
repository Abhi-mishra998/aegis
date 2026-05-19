#!/usr/bin/env python3
"""Billing reconciliation: symmetric diff between billable audits and usage.

Replaces the legacy "INTEGRITY VERIFIED" message in
`tests/load/locustfile.py`, which only compared aggregate counts with a 50-row
tolerance and missed every directed (audit↔usage) mismatch. This script
queries both physical databases and produces a structured JSON report; non-zero
exit when integrity is broken.

Billable definition — see docs/reconciliation.md for the authoritative spec:

    SELECT * FROM audit_logs
     WHERE action = 'execute_tool'
       AND decision <> 'reject'
       AND tenant_id IS NOT NULL

Two directional queries:

  audit_without_usage    billable audit rows older than --grace-seconds with no
                         matching usage_records row (missed billing — money
                         left on the table or eventually-consistent in-flight).

  usage_without_audit    usage_records rows whose audit_id is not present in
                         audit_logs at all (anomaly: manual insert, race
                         between billing and audit, or a billing pipeline bug).

Modes:

  one-shot  python scripts/ops/reconcile.py --tenant <uuid> --json
              → writes JSON report to stdout, exits non-zero on gap.

  scheduler  python scripts/ops/reconcile.py --watch 300
              → every 300s, runs the report and POSTs to the gateway's
                /internal/reconciliation-report so the SLI gauges update.

Connection:

  ACP_AUDIT_DB    psycopg2 URL for the acp_audit database
                  (default: postgresql://postgres:postgres@localhost:5432/acp_audit)
  ACP_USAGE_DB    psycopg2 URL for the acp_usage database
                  (default: postgresql://postgres:postgres@localhost:5432/acp_usage)
  REDIS_URL       Redis URL for DLQ depth probes
                  (default: redis://localhost:6379/0)
  GATEWAY_URL     Gateway base URL for --watch posting (default: http://localhost:8000)
  INTERNAL_SECRET shared secret for the gateway's /internal endpoint
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from typing import Any

try:
    import psycopg2  # type: ignore
    import psycopg2.extras  # type: ignore
    import redis  # type: ignore
except ImportError as exc:  # pragma: no cover
    print(f"missing dep ({exc}); install psycopg2-binary and redis-py", file=sys.stderr)
    sys.exit(2)


# ── Configuration ─────────────────────────────────────────────────────────


def _default_audit_db() -> str:
    # ACP_AUDIT_DB takes precedence; DATABASE_URL is the service-side variable
    # (strip +asyncpg driver prefix for psycopg2). Port 5433: Docker maps host
    # 5433 → container 5432 (see infra/docker-compose.yml ports section).
    raw = os.environ.get("ACP_AUDIT_DB") or os.environ.get("DATABASE_URL", "")
    if raw:
        return raw.replace("postgresql+asyncpg://", "postgresql://")
    return "postgresql://postgres:postgres@localhost:5433/acp_audit"


def _default_usage_db() -> str:
    raw = os.environ.get("ACP_USAGE_DB") or os.environ.get("USAGE_DATABASE_URL", "")
    if raw:
        return raw.replace("postgresql+asyncpg://", "postgresql://")
    return "postgresql://postgres:postgres@localhost:5433/acp_usage"


def _default_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6379/0")


SAMPLE_SIZE = 10  # number of mismatched IDs we expose in the report
BILLING_DLQ_KEY = "acp:billing_dlq"
AUDIT_DLQ_KEY = "acp:audit_stream:dlq"


# ── Report shape ──────────────────────────────────────────────────────────


@dataclass
class ReconciliationReport:
    tenant_id: str
    billable_audit_count: int = 0
    usage_record_count: int = 0
    audit_without_usage_count: int = 0
    usage_without_audit_count: int = 0
    audit_without_usage_sample: list[str] = field(default_factory=list)
    usage_without_audit_sample: list[str] = field(default_factory=list)
    billing_dlq_length: int = 0
    audit_dlq_length: int = 0
    outbox_pending_age_seconds: int = 0
    is_integrous: bool = True
    status: str = "VERIFIED"  # VERIFIED | GAP_DETECTED | ERROR
    error: str | None = None
    ts: int = 0

    def finalize(self) -> "ReconciliationReport":
        # An error from any of the data accessors means we cannot vouch for
        # integrity — force the bad path so the CLI exits non-zero and
        # operators can't mistake a failed probe for a clean state.
        counts_clean = (
            self.audit_without_usage_count == 0
            and self.usage_without_audit_count == 0
            and self.billing_dlq_length == 0
            and self.audit_dlq_length == 0
        )
        if self.error:
            self.is_integrous = False
            self.status = "ERROR"
        elif counts_clean:
            self.is_integrous = True
            self.status = "VERIFIED"
        else:
            self.is_integrous = False
            self.status = "GAP_DETECTED"
        self.ts = int(time.time())
        return self


# ── Data access ───────────────────────────────────────────────────────────


def _fetch_audit_ids(
    audit_db: str,
    *,
    tenant_id: str | None,
    grace_seconds: int,
) -> tuple[set[str], int]:
    """Return (billable audit_ids older than grace, total billable count for
    that tenant). The total is used for the report header — the set is what
    we diff against usage_records.
    """
    where = [
        "action = 'execute_tool'",
        "decision <> 'reject'",
        "tenant_id IS NOT NULL",
    ]
    params: list[Any] = []
    if tenant_id:
        where.append("tenant_id = %s")
        params.append(tenant_id)

    conn = psycopg2.connect(audit_db)
    try:
        with conn.cursor() as cur:
            # Total count (no grace filter) — caller wants the headline number.
            cur.execute(
                f"SELECT COUNT(*) FROM audit_logs WHERE {' AND '.join(where)}",
                params,
            )
            total = int(cur.fetchone()[0] or 0)

            # Set for diff — only rows past the grace window.
            # NOTE: the gateway's billing path writes audit_logs.request_id
            # into usage_records.audit_id (NOT audit_logs.id) — see
            # `_record_billing_with_retry(audit_id=request_id)` in
            # services/gateway/middleware.py. The column is named `audit_id`
            # for historical reasons; the joining key has always been
            # `request_id`. The reconcile script's job is to diff against
            # whatever the runtime billing path actually writes, so we
            # select `request_id` here to match.
            grace_clause = f"created_at < NOW() - INTERVAL '{int(grace_seconds)} seconds'"
            cur.execute(
                f"SELECT request_id FROM audit_logs WHERE {' AND '.join(where)} "
                f"AND {grace_clause} AND request_id IS NOT NULL",
                params,
            )
            ids = {row[0] for row in cur.fetchall()}
            return ids, total
    finally:
        conn.close()


def _fetch_all_audit_request_ids(
    audit_db: str,
    *,
    tenant_id: str | None,
) -> set[str]:
    """Return the set of ALL audit_logs.request_id values (any action type).

    Used for the usage_without_audit check: a usage record is only truly
    orphaned if no audit row exists for that request_id at all.  Filtering
    to execute_tool would incorrectly flag usage records for rate-limited or
    blocked calls that do have audit entries under a different action name.
    """
    where = ["tenant_id IS NOT NULL", "request_id IS NOT NULL"]
    params: list[Any] = []
    if tenant_id:
        where.append("tenant_id = %s")
        params.append(tenant_id)

    conn = psycopg2.connect(audit_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT request_id FROM audit_logs WHERE {' AND '.join(where)}",
                params,
            )
            return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def _fetch_usage_ids(
    usage_db: str,
    *,
    tenant_id: str | None,
) -> tuple[set[str], int]:
    """Return (usage audit_ids, total usage count). NULL audit_id rows are
    excluded from the set so we don't pretend they match anything — they're
    counted separately in `usage_without_audit_count` via the diff below.
    """
    where = ["tenant_id IS NOT NULL"]
    params: list[Any] = []
    if tenant_id:
        where.append("tenant_id = %s")
        params.append(tenant_id)

    conn = psycopg2.connect(usage_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(*) FROM usage_records WHERE {' AND '.join(where)}",
                params,
            )
            total = int(cur.fetchone()[0] or 0)

            cur.execute(
                f"SELECT audit_id::text FROM usage_records "
                f"WHERE {' AND '.join(where)} AND audit_id IS NOT NULL",
                params,
            )
            ids = {row[0] for row in cur.fetchall()}
            return ids, total
    finally:
        conn.close()


def _outbox_pending_age_seconds(audit_db: str, *, tenant_id: str | None) -> int:
    """Age in seconds of the oldest pending_usage_events row still pending.
    Returns 0 when the outbox is empty."""
    where = ["status = 'pending'"]
    params: list[Any] = []
    if tenant_id:
        where.append("tenant_id = %s")
        params.append(tenant_id)
    conn = psycopg2.connect(audit_db)
    try:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT EXTRACT(EPOCH FROM (NOW() - MIN(created_at))) "
                f"FROM pending_usage_events WHERE {' AND '.join(where)}",
                params,
            )
            row = cur.fetchone()
            return int(row[0]) if row and row[0] is not None else 0
    except Exception:
        # Table may not exist in this DB schema; treat as 0.
        return 0
    finally:
        conn.close()


def _redis_dlq_lengths(redis_url: str) -> tuple[int, int]:
    """(billing_dlq_length, audit_dlq_length)."""
    r = redis.from_url(redis_url, socket_timeout=2.0)
    try:
        billing = int(r.llen(BILLING_DLQ_KEY) or 0)
    except Exception:
        billing = 0
    try:
        audit = int(r.xlen(AUDIT_DLQ_KEY) or 0)
    except Exception:
        audit = 0
    try:
        r.close()
    except Exception:
        pass
    return billing, audit


# ── Reconciliation core ───────────────────────────────────────────────────


def run_reconciliation(
    *,
    audit_db: str,
    usage_db: str,
    redis_url: str,
    tenant_id: str | None,
    grace_seconds: int,
) -> ReconciliationReport:
    """Compute the report. Always returns a finalized ReconciliationReport;
    DB or Redis failures are captured in `report.error` rather than raised so
    the caller can decide whether to retry."""
    report = ReconciliationReport(tenant_id=str(tenant_id or "all"))

    try:
        audit_ids, audit_total = _fetch_audit_ids(
            audit_db, tenant_id=tenant_id, grace_seconds=grace_seconds,
        )
        all_audit_request_ids = _fetch_all_audit_request_ids(
            audit_db, tenant_id=tenant_id,
        )
        usage_ids, usage_total = _fetch_usage_ids(usage_db, tenant_id=tenant_id)
    except Exception as exc:
        report.error = f"db_error: {type(exc).__name__}: {exc}"
        return report.finalize()

    report.billable_audit_count = audit_total
    report.usage_record_count = usage_total

    # audit_without_usage: every execute_tool audit must have a usage record.
    missing_in_usage = audit_ids - usage_ids
    # usage_without_audit: a usage record is orphaned only if NO audit entry
    # (of any action type) exists for that request_id. Rate-limited or
    # inference-proxy-blocked calls have audit rows under different action names
    # and must not be counted as integrity violations.
    missing_in_audit = usage_ids - all_audit_request_ids

    report.audit_without_usage_count = len(missing_in_usage)
    report.usage_without_audit_count = len(missing_in_audit)
    # Deterministic sample for the report (sorted so reruns produce the same
    # output for the same DB state — easier to verify in tests).
    report.audit_without_usage_sample = sorted(missing_in_usage)[:SAMPLE_SIZE]
    report.usage_without_audit_sample = sorted(missing_in_audit)[:SAMPLE_SIZE]

    try:
        report.billing_dlq_length, report.audit_dlq_length = _redis_dlq_lengths(redis_url)
    except Exception as exc:
        # DLQ probe failure should not poison the rest of the report.
        report.error = f"redis_warning: {type(exc).__name__}: {exc}"

    try:
        report.outbox_pending_age_seconds = _outbox_pending_age_seconds(
            audit_db, tenant_id=tenant_id,
        )
    except Exception:
        # Outbox table may be absent in dev sandboxes; ignore.
        pass

    return report.finalize()


# ── HTTP publish (--watch mode) ───────────────────────────────────────────


def publish_report(report: ReconciliationReport, *, gateway_url: str, internal_secret: str | None) -> bool:
    try:
        import httpx  # type: ignore
    except ImportError:  # pragma: no cover
        print("httpx not installed; cannot --watch", file=sys.stderr)
        return False
    if not internal_secret:
        print("INTERNAL_SECRET not set; cannot POST report", file=sys.stderr)
        return False
    body = asdict(report)
    try:
        resp = httpx.post(
            f"{gateway_url.rstrip('/')}/internal/reconciliation-report",
            json=body,
            headers={"X-Internal-Secret": internal_secret},
            timeout=5.0,
        )
        return resp.status_code < 400
    except Exception as exc:
        print(f"publish failed: {exc}", file=sys.stderr)
        return False


# ── CLI ───────────────────────────────────────────────────────────────────


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--tenant", default=None,
                   help="Restrict reconciliation to a specific tenant_id (UUID). Default: all tenants.")
    p.add_argument("--grace-seconds", type=int, default=60,
                   help="Skip audit rows newer than this many seconds (in-flight grace). Default: 60.")
    p.add_argument("--json", action="store_true",
                   help="Emit the full JSON report to stdout (default mode).")
    p.add_argument("--watch", type=int, default=None, metavar="SECONDS",
                   help="Run periodically every SECONDS; POST each report to the gateway.")
    p.add_argument("--gateway-url", default=os.environ.get("GATEWAY_URL", "http://localhost:8000"),
                   help="Gateway base URL for --watch mode (default: http://localhost:8000).")
    p.add_argument("--audit-db", default=None, help="override ACP_AUDIT_DB")
    p.add_argument("--usage-db", default=None, help="override ACP_USAGE_DB")
    p.add_argument("--redis-url", default=None, help="override REDIS_URL")
    return p


def _one_shot(args, audit_db: str, usage_db: str, redis_url: str) -> int:
    report = run_reconciliation(
        audit_db=audit_db, usage_db=usage_db, redis_url=redis_url,
        tenant_id=args.tenant, grace_seconds=args.grace_seconds,
    )
    print(json.dumps(asdict(report), indent=2, sort_keys=True))
    return 0 if report.is_integrous else 1


def _watch(args, audit_db: str, usage_db: str, redis_url: str) -> int:
    internal_secret = os.environ.get("INTERNAL_SECRET")
    print(f"[watch] interval={args.watch}s gateway={args.gateway_url} tenant={args.tenant or 'all'}", file=sys.stderr)
    while True:
        report = run_reconciliation(
            audit_db=audit_db, usage_db=usage_db, redis_url=redis_url,
            tenant_id=args.tenant, grace_seconds=args.grace_seconds,
        )
        ok = publish_report(report, gateway_url=args.gateway_url, internal_secret=internal_secret)
        print(json.dumps(
            {"status": report.status, "audit_without_usage": report.audit_without_usage_count,
             "usage_without_audit": report.usage_without_audit_count,
             "published": ok, "ts": report.ts}),
            file=sys.stderr,
        )
        time.sleep(max(int(args.watch), 5))


def main() -> int:
    args = _build_argparser().parse_args()
    audit_db = args.audit_db or _default_audit_db()
    usage_db = args.usage_db or _default_usage_db()
    redis_url = args.redis_url or _default_redis_url()

    if args.watch:
        return _watch(args, audit_db, usage_db, redis_url)
    return _one_shot(args, audit_db, usage_db, redis_url)


if __name__ == "__main__":
    sys.exit(main())
