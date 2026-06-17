"""Sprint 19 — High-risk prompt patterns that require human approval.

Founder mandate: when an employee tells Claude to "transfer $500,000 to
…" or "delete production namespace …" the proxy must NOT forward the
prompt to Anthropic. It writes an audit row with `decision='escalate'`
+ the required approver role, returns HTTP 202 with the approval ID,
and lets a human approve or reject via the existing Approval Inbox.

Patterns are deliberately conservative. We err toward escalating —
"the operator can always approve, but a wrongly-allowed action can't
be un-done." A few rules of thumb:

  - Money movement above $100k → CFO approval.
  - Production-infra destruction → SRE LEAD approval.
  - Mass-data operations (DROP TABLE, DELETE FROM users) → CISO.
  - PII bulk export → CISO.

This module is the single source of truth — both the LLM-proxy
(``services/gateway/routers/messages.py``) and the tool-call path can
import it. Keep regexes simple + case-insensitive; the matched pattern
name is what shows on the operator's Approval Inbox card.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class EscalationPattern:
    """One escalation rule."""
    id:             str           # short slug, shown on the inbox card
    label:          str           # one-line analyst-readable
    approver_role:  str           # CFO / CISO / SRE_LEAD / OWNER
    pattern:        re.Pattern[str]


_PATTERNS: tuple[EscalationPattern, ...] = (
    # ── Money movement above $100k ───────────────────────────────────
    EscalationPattern(
        id="wire_transfer_large",
        label="Money movement above $100k (CFO approval)",
        approver_role="CFO",
        # Matches "transfer $500,000", "send $250k", "wire 1.5M to …",
        # "pay 500000 to vendor". Allows comma + k/m suffix.
        pattern=re.compile(
            r"\b(?:transfer|send|wire|pay|disburse|remit|move)\s+"
            r"(?:about\s+|approximately\s+)?"
            r"\$?\s*([0-9]{1,3}(?:[,\.]?[0-9]{3})+(?:\.[0-9]+)?|"
            r"[0-9]+(?:\.[0-9]+)?\s*(?:k|m|million|thousand))\b",
            re.IGNORECASE,
        ),
    ),

    # ── Production-infra destruction ─────────────────────────────────
    EscalationPattern(
        id="prod_k8s_destruction",
        label="kubectl delete / drain on production (SRE LEAD approval)",
        approver_role="SRE_LEAD",
        pattern=re.compile(
            r"kubectl\s+(?:delete|drain|cordon|uncordon)\s+"
            r".*(?:prod|production|pet[-_]?clinic[-_]?prod|prd)\b",
            re.IGNORECASE,
        ),
    ),
    EscalationPattern(
        id="terraform_destroy_prod",
        label="terraform destroy on production (SRE LEAD approval)",
        approver_role="SRE_LEAD",
        pattern=re.compile(
            r"terraform\s+destroy.*(?:prod|production|prd)",
            re.IGNORECASE,
        ),
    ),

    # ── Mass-data operations ─────────────────────────────────────────
    EscalationPattern(
        id="mass_db_truncate",
        label="DROP TABLE / TRUNCATE on a tenant table (CISO approval)",
        approver_role="CISO",
        # Note: "DROP TABLE *" / "DELETE FROM users" are already DENIED
        # by the InjectionDetector mass_destruction pattern. The ones
        # ESCALATED here are TRUNCATE / DROP on a SPECIFIC table where
        # the operator's intent is genuinely ambiguous (could be a
        # cleanup migration). Force a human read.
        pattern=re.compile(
            r"\b(?:drop\s+table|truncate(?:\s+table)?)\s+"
            r"(?:if\s+exists\s+)?[\w\.\"`]+",
            re.IGNORECASE,
        ),
    ),

    # ── Bulk PII export ──────────────────────────────────────────────
    EscalationPattern(
        id="bulk_pii_export",
        label="Bulk PII export (CISO approval)",
        approver_role="CISO",
        pattern=re.compile(
            r"\b(?:export|download|dump|extract|get)\s+"
            r"(?:all|every|the\s+full|the\s+entire)\s+"
            r"(?:customer|user|patient|client|employee)\s+"
            r"(?:emails?|ssn|social|phone|addresses|records?|pii|data)\b",
            re.IGNORECASE,
        ),
    ),

    # ── Sensitive file access ────────────────────────────────────────
    # Note: /etc/passwd / id_rsa / .aws/credentials are already DENIED
    # by the path-traversal detector in the tool-call path. This rule
    # catches the LLM-proxy case where an employee asks the model to
    # *write* a credential file — different attack surface.
    EscalationPattern(
        id="credential_file_write",
        label="Writing a credential / private-key file (OWNER approval)",
        approver_role="OWNER",
        pattern=re.compile(
            r"\b(?:create|write|save|generate|put)\s+.*"
            r"(?:to|into|at|in)\s+"
            r"(?:[/~][\w\./-]*"
            r"(?:authorized_keys|id_rsa|\.aws/credentials|\.ssh/|\.pgpass))",
            re.IGNORECASE,
        ),
    ),
)


def scan(text: str) -> EscalationPattern | None:
    """Return the first matching escalation pattern, or None.

    Convention: callers MUST run the deny-path (InjectionDetector) FIRST
    — if a prompt is both injection-y AND big-spend, it should be denied
    not escalated. The deny path takes precedence.
    """
    if not text:
        return None
    for p in _PATTERNS:
        if p.pattern.search(text):
            return p
    return None


def by_id(pattern_id: str) -> EscalationPattern | None:
    for p in _PATTERNS:
        if p.id == pattern_id:
            return p
    return None


def all_patterns() -> tuple[EscalationPattern, ...]:
    return _PATTERNS
