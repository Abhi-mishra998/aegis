"""
Session Intelligence Engine — Sprint ADR-shift 2026-06-15.

L2 (action-semantics) catches a single bad tool call. L3 (slow-exfil)
catches a cumulative pattern on the same agent + table. **L4 Session
Intelligence** catches the *sequence*:

    schema_recon → pii_read → compression → external_post

Each step looks legitimate on its own. The chain is exfiltration.

This is the core difference between a Policy Engine and an Agent
Detection & Response (ADR) platform — CrowdStrike doesn't deny `rm -rf`,
it denies the *kill-chain* that leads to it.

Architecture:

1. Each `/execute` call gets classified into one of a small set of
   ACTION CLASSES (schema_recon, pii_read, bulk_pii_read, cred_read,
   compression, external_post, iac_destroy, k8s_destroy, priv_esc,
   benign).
2. The classification is appended to a per-session Redis stream
   `acp:session:{session_id}:actions` (X-Session-ID header).
3. After append we scan the last N (=20) actions for known attack
   chains via `match_attack_chain()`.
4. The match (if any) is surfaced as a string in
   `tool_metadata.arguments.attack_chain` so the policy layer (rego +
   Python port) can fold it into the deny decision.

The classifier and matcher are intentionally tiny + deterministic — no
ML. Real ML lives in services/behavior. The point here is a clean,
debuggable signal a buyer can read in an audit row.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from redis.asyncio import Redis as _Redis

SESSION_WINDOW = 20            # last-N actions scanned for chain matches
SESSION_TTL_SECONDS = 7200     # keep 2h of session history

# ---------------------------------------------------------------------------
# 1. Action classifier
# ---------------------------------------------------------------------------
_SCHEMA_RECON_PATTERNS = (
    "information_schema",
    "show tables",
    "show databases",
    "describe ",
    "pg_catalog",
    "sys.tables",
    "sys.columns",
    "sqlite_master",
    "kubectl get",
    "kubectl describe",
    "aws iam list",
    "aws s3 ls",
)
_PII_TABLES = (
    "customer", "customers", "user", "users",
    "account", "accounts", "patient", "patients",
    "applicant", "applicants", "employee", "employees",
)
_CRED_PATHS = (
    "/etc/passwd", "/etc/shadow", "/root/.ssh", "/.ssh/id_",
    "/root/.aws/credentials", "/.aws/credentials",
    "/proc/self", "/sys/firmware",
    ".pem", ".key", "private_key",
)
_COMPRESSION_PATTERNS = (
    "tar czf", "tar -czf", "tar zcf",
    "zip ", "7z a ", "gzip ", "xz ", "bzip2 ",
)
# Phase-2 cleanup — the inline tuple is now derived from
# services/policy/pattern_catalog.py. EXTERNAL_EGRESS_HOSTS holds the
# suspect outbound hosts; PERSONAL_EMAIL_DOMAINS holds the free-webmail
# domains used by the sendmail-to-personal-account detector. The session
# intelligence engine wants the union of both because its single test
# does host-match across email + web destinations.
from services.policy.pattern_catalog import (
    EXTERNAL_EGRESS_HOSTS as _EGRESS_HOSTS,
    PERSONAL_EMAIL_DOMAINS as _PERSONAL_EMAIL,
)
_EXTERNAL_HOSTS = tuple(_PERSONAL_EMAIL) + tuple(_EGRESS_HOSTS)
_IAC_DESTROY = (
    "terraform destroy", "pulumi destroy", "cdk destroy",
    "terraform down", "pulumi down",
)
_K8S_DESTROY_PROD = (
    "kubectl delete namespace prod",
    "kubectl delete ns prod",
    "kubectl delete deploy",
)
_PRIV_ESC = (
    "sudo ", "su -", "chmod +s",
    "setcap ", "useradd ", "usermod -a -G",
    "aws iam attach-role-policy", "aws iam create-access-key",
)


def classify_action(
    *,
    tool: str,
    query_norm: str = "",
    command_norm: str = "",
    path: str = "",
    url: str = "",
    raw_norm: str = "",
    row_limit: int = -1,
    contains_pii: bool = False,
) -> str:
    """Map one /execute call onto a single action class. Deterministic;
    no ML. Order matters — first matching class wins."""

    qry = (query_norm or "").lower()
    cmd = (command_norm or "").lower()
    p   = (path or "").lower()
    u   = (url or "").lower()
    raw = (raw_norm or "").lower()

    # 1. Schema reconnaissance
    for sig in _SCHEMA_RECON_PATTERNS:
        if sig in qry or sig in cmd:
            return "schema_recon"

    # 2. Credential / sensitive-path read
    for sig in _CRED_PATHS:
        if sig in p or sig in cmd or sig in raw:
            return "cred_read"

    # 3. PII read (SELECT against a PII table)
    if qry.startswith("select "):
        for t in _PII_TABLES:
            if f" from {t} " in qry + " " or qry.endswith(f" from {t}") or f" from {t}," in qry:
                if row_limit > 1000 or row_limit < 0:
                    return "bulk_pii_read"
                return "pii_read"
        # SQL touches PII columns (ssn, credit_card, etc.)
        if contains_pii:
            return "bulk_pii_read" if (row_limit > 100 or row_limit < 0) else "pii_read"

    # 4. Compression (precursor to exfil)
    for sig in _COMPRESSION_PATTERNS:
        if sig in cmd:
            return "compression"

    # 5. External POST / outbound to risky host
    if u.startswith("https://") or u.startswith("http://"):
        for h in _EXTERNAL_HOSTS:
            if h in u:
                return "external_post"
    for h in _EXTERNAL_HOSTS:
        if h in raw or h in cmd:
            return "external_post"

    # 6. IaC destroy
    for sig in _IAC_DESTROY:
        if sig in cmd:
            return "iac_destroy"

    # 7. K8s destroy (prod-labelled)
    for sig in _K8S_DESTROY_PROD:
        if sig in cmd:
            return "k8s_destroy"

    # 8. Privilege escalation
    for sig in _PRIV_ESC:
        if sig in cmd or sig in raw:
            return "priv_esc"

    return "benign"


# ---------------------------------------------------------------------------
# 2. Attack-chain matcher
# ---------------------------------------------------------------------------
# Each chain is a *contiguous* sequence in the last-N window. Longer chains
# imply higher confidence + severity. Severity goes:
#
#   "deny"     → hard-deny + immediate auto-quarantine (Workstream B.P2)
#   "escalate" → operator approval (existing escalate path)
#
# Chains are matched in declared order; the FIRST match wins (most specific
# / most severe at the top of the list).

_CHAINS = [
    # The textbook exfiltration kill chain.
    ("exfil_clear_pii_compress_post",
     ("schema_recon", "pii_read", "compression", "external_post"),
     "deny"),
    # Credential theft via outbound.
    ("cred_theft",
     ("cred_read", "external_post"),
     "deny"),
    # Bulk PII export to external destination.
    ("bulk_pii_to_external",
     ("bulk_pii_read", "external_post"),
     "deny"),
    # Reconnaissance into privilege escalation — likely insider/attacker.
    ("recon_priv_esc",
     ("schema_recon", "priv_esc"),
     "escalate"),
    # Repeated PII reads followed by compression — pre-exfil.
    ("pii_recon_compress",
     ("pii_read", "pii_read", "compression"),
     "escalate"),
    # Schema → PII → cred read — staging for attack.
    ("recon_then_cred",
     ("schema_recon", "pii_read", "cred_read"),
     "escalate"),
]


def match_attack_chain(actions: list[str]) -> tuple[str, str] | None:
    """Scan the action sequence for a known kill chain. Returns
    (chain_name, severity) for the most specific match, or None."""
    if not actions:
        return None
    for name, pattern, severity in _CHAINS:
        # Look for the pattern as a contiguous subsequence anywhere in the
        # window. This is forgiving: `schema_recon, benign, pii_read, …`
        # will NOT match `(schema_recon, pii_read, …)` because we look
        # for contiguous matches. Buyer's intuition: chain steps must be
        # close together in time.
        plen = len(pattern)
        for i in range(len(actions) - plen + 1):
            if tuple(actions[i:i + plen]) == pattern:
                return name, severity
    return None


# ---------------------------------------------------------------------------
# 3. Session accumulator (Redis-backed)
# ---------------------------------------------------------------------------
async def record_session_action(
    redis: "_Redis",
    *,
    session_id: str,
    action_class: str,
) -> list[str]:
    """Append the action class to the session list and return the trailing
    SESSION_WINDOW elements (most recent last). Best-effort; on Redis
    failure returns [action_class] so the caller can still classify the
    single-step case."""
    if not session_id:
        return [action_class]
    key = f"acp:session:{session_id}:actions"
    try:
        pipe = redis.pipeline()
        pipe.rpush(key, action_class)
        pipe.ltrim(key, -SESSION_WINDOW, -1)
        pipe.expire(key, SESSION_TTL_SECONDS)
        pipe.lrange(key, 0, -1)
        results = await pipe.execute()
        raw = results[-1] or []
    except Exception:
        return [action_class]
    out = []
    for b in raw:
        if isinstance(b, (bytes, bytearray)):
            out.append(b.decode("utf-8", "replace"))
        else:
            out.append(str(b))
    return out
