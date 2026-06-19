"""Sprint 23 — Compliance Policy Packs.

Single source of truth for the five sales-grade packs the founder
flagged as priority #5:

    SOC2  / PCI  / HIPAA  / Finance  / DevOps

Each pack is one ``PolicyPack`` instance carrying:

    framework_controls       — e.g. ["SOC2:CC6.1", "SOC2:CC7.2"]. Used
                                by the Compliance page to badge the
                                control as 'enforced by the SOC2 Pack'
                                when the tenant has the pack enabled.
    extra_escalation_patterns— ``EscalationPattern`` rules that get
                                OR-ed into the base scan from
                                services/gateway/escalation_patterns.
                                Lets the founder say 'in HIPAA mode,
                                any "patient record" prompt routes to
                                the CISO Inbox' without the customer
                                writing Rego.
    default_capabilities     — capability ids (Sprint 13 vocabulary)
                                that should be auto-suggested in the
                                onboarding wizard once a pack is on.

Wire-in points:

    1.  /policy-packs/catalog     — the wizard + Settings tab render
                                    this verbatim.
    2.  /workspace/policy-packs   — per-tenant enabled list (GET/PUT).
    3.  /v1/messages + /v1/chat/completions — the escalation scan
        consults the enabled packs in addition to the base patterns.

This module is intentionally regex-only (same shape as
services/gateway/escalation_patterns) so adding a new pack is one
diff and never requires a service redeploy of the policy engine.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from services.gateway.escalation_patterns import EscalationPattern


@dataclass(frozen=True)
class PolicyPack:
    id:                          str
    label:                       str
    blurb:                       str
    framework_controls:          tuple[str, ...]
    default_capabilities:        tuple[str, ...]
    extra_escalation_patterns:   tuple[EscalationPattern, ...]


_SOC2 = PolicyPack(
    id="SOC2",
    label="SOC 2 (Type II)",
    blurb=(
        "Audit-log integrity, change management, access-control. "
        "Routes prompts that touch logs, credentials, or production "
        "secrets to the CISO Inbox."
    ),
    framework_controls=(
        "SOC2:CC6.1",   # Logical access
        "SOC2:CC7.2",   # System monitoring
        "SOC2:CC8.1",   # Change management
    ),
    default_capabilities=("filesystem", "database", "infrastructure"),
    extra_escalation_patterns=(
        EscalationPattern(
            id="soc2_audit_log_tamper",
            label="SOC 2 — modifying or deleting audit logs (CISO approval)",
            approver_role="CISO",
            pattern=re.compile(
                r"\b(?:delete|truncate|drop|modify|tamper|alter|clear)\s+"
                r"(?:\S+\s+){0,4}?"     # 0-4 adjectives/articles
                r"(?:audit\s+log|audit_log|audit\s+trail|access\s+log)",
                re.IGNORECASE,
            ),
        ),
        EscalationPattern(
            id="soc2_rotate_secret_unticketed",
            label="SOC 2 — rotate production secret without a change ticket (CISO approval)",
            approver_role="CISO",
            pattern=re.compile(
                r"\b(?:rotate|reset|regenerate|change)\s+"
                r"(?:\S+\s+){0,4}?"     # 0-4 adjectives/articles
                r"(?:secret|api[\s-]?key|token|password|credentials?)\b",
                re.IGNORECASE,
            ),
        ),
    ),
)


_PCI = PolicyPack(
    id="PCI",
    label="PCI DSS v4",
    blurb=(
        "Card-data protection. Routes prompts that read cardholder data, "
        "encryption keys, or PAN numbers to the CISO Inbox."
    ),
    framework_controls=(
        "PCI:3.2",   # Do not store sensitive auth data after authorization
        "PCI:4.1",   # Encryption in transit
        "PCI:8.3",   # Strong cryptography for non-console admin
        "PCI:10.5",  # Audit-trail protection
    ),
    default_capabilities=("database", "external_apis"),
    extra_escalation_patterns=(
        EscalationPattern(
            id="pci_pan_read",
            label="PCI — read full PAN / card number (CISO approval)",
            approver_role="CISO",
            pattern=re.compile(
                r"\b(?:show|reveal|print|display|return|select|read)\s+"
                r"(?:\S+\s+){0,5}?"     # 0-5 adjective/article tokens
                r"(?:pan|card[\s_-]?number|credit\s+card|primary\s+account\s+number|cardholder\s+data)",
                re.IGNORECASE,
            ),
        ),
        EscalationPattern(
            id="pci_encryption_key_export",
            label="PCI — export an encryption / signing key (CISO approval)",
            approver_role="CISO",
            pattern=re.compile(
                r"\b(?:export|download|copy|extract|cat|read)\s+"
                r"(?:\S+\s+){0,5}?"     # 0-5 adjective/article tokens
                r"(?:encryption\s+key|signing\s+key|private\s+key|kms\s+key|hsm\s+key|pem\s+file)",
                re.IGNORECASE,
            ),
        ),
    ),
)


_HIPAA = PolicyPack(
    id="HIPAA",
    label="HIPAA (Privacy + Security)",
    blurb=(
        "PHI protection. Routes prompts that touch patient records, "
        "diagnoses, prescriptions, or medical-history data to the CISO Inbox."
    ),
    framework_controls=(
        "HIPAA:164.308(a)(1)",
        "HIPAA:164.312(b)",
        "HIPAA:164.312(e)",
    ),
    default_capabilities=("database", "email"),
    extra_escalation_patterns=(
        EscalationPattern(
            id="hipaa_phi_export",
            label="HIPAA — bulk patient PHI export (CISO approval)",
            approver_role="CISO",
            pattern=re.compile(
                r"\b(?:export|download|dump|extract|select|show)\s+"
                r"(?:all\s+|every\s+|the\s+full\s+|the\s+entire\s+)?"
                r"(?:patient|client|member)\s+"
                r"(?:records?|histor(?:y|ies)|charts?|diagnos[ie]s|prescription|phi|ehr|emr|notes?)",
                re.IGNORECASE,
            ),
        ),
        EscalationPattern(
            id="hipaa_phi_email_outside_ba",
            label="HIPAA — email PHI to address outside the Business Associate list (CISO approval)",
            approver_role="CISO",
            pattern=re.compile(
                r"\b(?:email|send|mail|forward)\s+"
                r"(?:the\s+|all\s+)?"
                r"(?:patient|diagnos[ie]s|phi|medical\s+record)"
                r".*?@",
                re.IGNORECASE | re.DOTALL,
            ),
        ),
    ),
)


_FINANCE = PolicyPack(
    id="Finance",
    label="Finance / Treasury",
    blurb=(
        "Tighter money-movement thresholds. Routes ANY transfer / wire / "
        "ACH and any GL journal entry to the CFO Inbox — overrides the "
        "default $100k threshold."
    ),
    framework_controls=(
        "SOX:404",
        "Finance:WireCap",
        "Finance:JournalEntry",
    ),
    default_capabilities=("payments", "database"),
    extra_escalation_patterns=(
        EscalationPattern(
            id="finance_any_wire_or_ach",
            label="Finance — any wire / ACH / transfer regardless of amount (CFO approval)",
            approver_role="CFO",
            pattern=re.compile(
                r"\b(?:wire|ach|transfer|disburse|remit|originate)\s+"
                r"(?:\S+\s+){0,5}?"     # 0-5 tokens — amount, currency, etc.
                r"(?:funds?|payment|money|usd|dollars|eur|gbp|cad|jpy|to)",
                re.IGNORECASE,
            ),
        ),
        EscalationPattern(
            id="finance_journal_entry",
            label="Finance — post or reverse a general-ledger journal entry (CFO approval)",
            approver_role="CFO",
            pattern=re.compile(
                r"\b(?:post|reverse|book|adjust|create)\s+"
                r"(?:a\s+|the\s+|new\s+)?"
                r"(?:journal\s+entry|gl\s+entry|je\b|adjusting\s+entry)",
                re.IGNORECASE,
            ),
        ),
        EscalationPattern(
            id="finance_invoice_approval",
            label="Finance — approve or release a vendor invoice (CFO approval)",
            approver_role="CFO",
            pattern=re.compile(
                r"\b(?:approve|release|pay|authorize)\s+"
                r"(?:the\s+|a\s+|new\s+|all\s+)?"
                r"(?:vendor\s+)?invoice",
                re.IGNORECASE,
            ),
        ),
    ),
)


_DEVOPS = PolicyPack(
    id="DevOps",
    label="DevOps / Platform",
    blurb=(
        "Production-safety controls. Routes prompts that touch prod K8s, "
        "force-push to main, rollback a release, or skip CI gates to the "
        "SRE LEAD Inbox."
    ),
    framework_controls=(
        "ISO27001:A.12",
        "NIST-CSF:PR.IP-1",
        "DevOps:ChangeManagement",
    ),
    default_capabilities=("infrastructure",),
    extra_escalation_patterns=(
        EscalationPattern(
            id="devops_force_push_main",
            label="DevOps — force push to main / master (SRE LEAD approval)",
            approver_role="SRE_LEAD",
            pattern=re.compile(
                r"git\s+push\s+(?:-f|--force)\s+\S*\s*(?:main|master|trunk)",
                re.IGNORECASE,
            ),
        ),
        EscalationPattern(
            id="devops_rollback_prod",
            label="DevOps — rollback / revert a production release (SRE LEAD approval)",
            approver_role="SRE_LEAD",
            pattern=re.compile(
                r"\b(?:rollback|revert|downgrade)\s+"
                r"(?:the\s+|all\s+)?"
                r"(?:prod|production|release|deploy(?:ment)?)",
                re.IGNORECASE,
            ),
        ),
        EscalationPattern(
            id="devops_skip_ci",
            label="DevOps — bypass / skip CI gates (SRE LEAD approval)",
            approver_role="SRE_LEAD",
            pattern=re.compile(
                r"\b(?:skip|bypass|disable|--no-verify)\s+"
                r"(?:the\s+)?"
                r"(?:ci|tests?|pre[\s-]?commit|signature|gpg|review)",
                re.IGNORECASE,
            ),
        ),
    ),
)


_AI_STARTUP_GENERIC = PolicyPack(
    id="AI_STARTUP_GENERIC",
    label="AI Startup (generic safe defaults)",
    blurb=(
        "Minimal safety net for AI-native startups with no specific "
        "regulatory tier. Prompt-injection denies + budget caps + path "
        "traversal denies. Escalates anything that looks like exfil to "
        "the OWNER."
    ),
    framework_controls=(
        "AI_STARTUP:promp-injection-baseline",
        "AI_STARTUP:budget-cap-baseline",
    ),
    default_capabilities=("database",),
    extra_escalation_patterns=(
        EscalationPattern(
            id="ai_startup_data_exfil",
            label="AI Startup — exfil-shaped prompt (OWNER approval)",
            approver_role="OWNER",
            pattern=re.compile(
                r"\b(?:upload|send|email|post|sync|export|dump)\s+"
                r"(?:\S+\s+){0,4}?"
                r"(?:customer|user|account|payment|credit)\s+"
                r"(?:data|list|table|database|record)",
                re.IGNORECASE,
            ),
        ),
        EscalationPattern(
            id="ai_startup_credential_read",
            label="AI Startup — credential file read (OWNER approval)",
            approver_role="OWNER",
            pattern=re.compile(
                r"(?:\.env(?:\.\w+)?|credentials\.json|id_rsa|api[_-]?keys?\.txt)\b",
                re.IGNORECASE,
            ),
        ),
    ),
)


_PACKS: tuple[PolicyPack, ...] = (_SOC2, _PCI, _HIPAA, _FINANCE, _DEVOPS, _AI_STARTUP_GENERIC)
_BY_ID: dict[str, PolicyPack] = {p.id: p for p in _PACKS}

KNOWN_PACK_IDS: tuple[str, ...] = tuple(p.id for p in _PACKS)


def all_packs() -> tuple[PolicyPack, ...]:
    return _PACKS


def get(pack_id: str) -> PolicyPack | None:
    return _BY_ID.get(pack_id)


def scan_for_pack_escalation(
    text: str, enabled_packs: list[str],
) -> tuple[EscalationPattern, str] | None:
    """Run every enabled pack's extra patterns against ``text``.

    Returns ``(matched_pattern, pack_id)`` on first hit, or ``None``.
    The caller (services/gateway/routers/messages.py + openai_messages.py)
    runs the base ``escalation_patterns.scan`` first; this function is
    consulted only if the base scan returned no match, so packs ADD
    coverage without overriding the founder's default rules.
    """
    if not text or not enabled_packs:
        return None
    seen: set[str] = set()
    for pid in enabled_packs:
        if pid in seen:
            continue
        seen.add(pid)
        pack = _BY_ID.get(pid)
        if pack is None:
            continue
        for pat in pack.extra_escalation_patterns:
            if pat.pattern.search(text):
                return (pat, pack.id)
    return None
