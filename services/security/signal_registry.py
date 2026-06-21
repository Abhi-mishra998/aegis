"""
Sprint 1 — Security Signal Registry.

Single source of truth for every security signal Aegis emits. Every signal
carries:

    id                  — stable identifier (e.g. "privilege_escalation_attempt").
                          The same string the canonical model puts into
                          signal_findings and the response body.
    objective           — SecurityObjective enum value (which MITRE tactic this
                          serves: PRIVILEGE_ESCALATION, CREDENTIAL_ACCESS, …).
    severity            — Severity enum: LOW / MEDIUM / HIGH / CRITICAL.
    mitre_tactic        — MITRE ATT&CK tactic ID (TA00xx).
    mitre_technique     — MITRE technique ID (T1xxx[.xxx]) with human label.
    default_score       — inherent risk on 0-100 (used by canonical and the
                          cumulative risk pipeline).
    default_response    — what the engine should do when this signal fires in
                          isolation: "monitor" / "escalate" / "deny" / "quarantine".
    description         — one-line explanation, surfaced into the response so
                          the SOC analyst sees WHY.

The registry is read-only at runtime. New signals → add an entry here. No
other file in the codebase should hold a signal-name → score / tier mapping.

Why a registry rather than dispersed constants:

    * One file to grep when you want to know "what signals exist?"
    * One file to update for SOC integration (SIEM techniques, dashboards).
    * One file the rego/OPA path can serialise from (Sprint 8 — closes the
      Python/Rego drift gap).
    * One file unit-tested for "every signal canonical actually emits is
      registered" — catches drift the moment it happens.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable


# ---------------------------------------------------------------------------
# MITRE-aligned security objectives. Each Aegis signal belongs to exactly one.
# Source: https://attack.mitre.org/
# ---------------------------------------------------------------------------
class SecurityObjective(str, Enum):
    INITIAL_ACCESS       = "initial_access"        # TA0001
    PERSISTENCE          = "persistence"           # TA0003
    PRIVILEGE_ESCALATION = "privilege_escalation"  # TA0004
    DEFENSE_EVASION      = "defense_evasion"       # TA0005
    CREDENTIAL_ACCESS    = "credential_access"     # TA0006
    DISCOVERY            = "discovery"             # TA0007
    COLLECTION           = "collection"            # TA0009
    EXFILTRATION         = "exfiltration"          # TA0010
    IMPACT               = "impact"                # TA0040


_OBJECTIVE_TO_TACTIC = {
    SecurityObjective.INITIAL_ACCESS:       "TA0001",
    SecurityObjective.PERSISTENCE:          "TA0003",
    SecurityObjective.PRIVILEGE_ESCALATION: "TA0004",
    SecurityObjective.DEFENSE_EVASION:      "TA0005",
    SecurityObjective.CREDENTIAL_ACCESS:    "TA0006",
    SecurityObjective.DISCOVERY:            "TA0007",
    SecurityObjective.COLLECTION:           "TA0009",
    SecurityObjective.EXFILTRATION:         "TA0010",
    SecurityObjective.IMPACT:               "TA0040",
}


class Severity(str, Enum):
    LOW      = "LOW"
    MEDIUM   = "MEDIUM"
    HIGH     = "HIGH"
    CRITICAL = "CRITICAL"


# Closed set — keep in sync with the policy engine's tier vocabulary.
_VALID_RESPONSES = {"monitor", "escalate", "deny", "quarantine"}


@dataclass(frozen=True)
class SignalDefinition:
    """One row in the signal registry.

    Frozen so a caller can't mutate the canonical definition by accident.
    All fields are required — incomplete entries fail closed at module load.
    """
    id:               str
    objective:        SecurityObjective
    severity:         Severity
    mitre_technique:  str   # full "T1xxx[.xxx] Name" string
    default_score:    int   # 0-100, inherent risk
    default_response: str   # one of _VALID_RESPONSES
    description:      str

    @property
    def mitre_tactic(self) -> str:
        """Auto-derived from objective. Avoids two-source drift."""
        return _OBJECTIVE_TO_TACTIC[self.objective]

    @property
    def mitre_technique_id(self) -> str:
        """Just the T1xxx[.xxx] portion, no human label. Useful for SIEM tags."""
        return self.mitre_technique.split(" ", 1)[0]

    def __post_init__(self) -> None:
        # Sanity guards — fail at import time, not on first /execute.
        if not (0 <= self.default_score <= 100):
            raise ValueError(
                f"signal {self.id}: default_score must be 0-100, got {self.default_score}"
            )
        if self.default_response not in _VALID_RESPONSES:
            raise ValueError(
                f"signal {self.id}: default_response must be one of "
                f"{sorted(_VALID_RESPONSES)}, got {self.default_response!r}"
            )
        if not self.mitre_technique.startswith("T"):
            raise ValueError(
                f"signal {self.id}: mitre_technique must start with T (got "
                f"{self.mitre_technique!r})"
            )


# ---------------------------------------------------------------------------
# The registry. Keep alphabetised by id within each objective group; one
# block per MITRE tactic. New signals → add here, nowhere else.
# ---------------------------------------------------------------------------
_REGISTRY: dict[str, SignalDefinition] = {}


def _register(sig: SignalDefinition) -> None:
    if sig.id in _REGISTRY:
        raise ValueError(f"duplicate signal id: {sig.id}")
    _REGISTRY[sig.id] = sig


# ───── INITIAL ACCESS (TA0001) ─────────────────────────────────────────────
_register(SignalDefinition(
    id="sql_injection_detected",
    objective=SecurityObjective.INITIAL_ACCESS,
    severity=Severity.CRITICAL,
    mitre_technique="T1190 Exploit Public-Facing Application",
    default_score=95, default_response="deny",
    description="Query carries SQL injection payload (UNION, stacked DROP, tautology, comment evasion).",
))

# P0-1 fix 2026-06-21: SSRF detector family. Brutal-review found that
# `http.get` with url=file:///etc/passwd / 169.254.169.254 / localhost
# returned action=allow with risk=0.0 — the canonical normalizer had no
# detector for these. Three flavours map to three findings so SOC can
# triage by attack class.
_register(SignalDefinition(
    id="ssrf_local_file",
    objective=SecurityObjective.INITIAL_ACCESS,
    severity=Severity.CRITICAL,
    mitre_technique="T1552.001 Unsecured Credentials: Credentials In Files",
    default_score=95, default_response="deny",
    description="HTTP tool call with file:// URL — local-file read via SSRF.",
))
_register(SignalDefinition(
    id="ssrf_cloud_metadata",
    objective=SecurityObjective.INITIAL_ACCESS,
    severity=Severity.CRITICAL,
    mitre_technique="T1552.005 Unsecured Credentials: Cloud Instance Metadata API",
    default_score=95, default_response="deny",
    description="HTTP tool call to cloud metadata endpoint (169.254.169.254, metadata.google.internal, etc).",
))
_register(SignalDefinition(
    id="ssrf_internal_network",
    objective=SecurityObjective.INITIAL_ACCESS,
    severity=Severity.HIGH,
    mitre_technique="T1190 Exploit Public-Facing Application",
    default_score=80, default_response="deny",
    description="HTTP tool call targeting RFC1918 / loopback / link-local / *.internal / *.local — pivoting into private network.",
))

# ───── PERSISTENCE (TA0003) ────────────────────────────────────────────────
_register(SignalDefinition(
    id="credential_artifact_write",
    objective=SecurityObjective.PERSISTENCE,
    severity=Severity.HIGH,
    mitre_technique="T1098.004 SSH Authorized Keys",
    default_score=75, default_response="deny",
    description="Shell or file_write that drops a credential-shaped artefact (.creds, authorized_keys, id_rsa).",
))

# ───── PRIVILEGE ESCALATION (TA0004) ───────────────────────────────────────
_register(SignalDefinition(
    id="privilege_escalation_attempt",
    objective=SecurityObjective.PRIVILEGE_ESCALATION,
    severity=Severity.CRITICAL,
    mitre_technique="T1098.001 Additional Cloud Credentials",
    default_score=95, default_response="deny",
    description="Identity-table write that elevates a principal to admin / superuser / root.",
))
_register(SignalDefinition(
    id="identity_table_write",
    objective=SecurityObjective.PRIVILEGE_ESCALATION,
    severity=Severity.HIGH,
    mitre_technique="T1136.001 Create Account: Local Account",
    default_score=60, default_response="escalate",
    description="INSERT / UPDATE / DELETE / GRANT against users / roles / accounts / permissions tables.",
))
_register(SignalDefinition(
    id="privilege_url_access",
    objective=SecurityObjective.PRIVILEGE_ESCALATION,
    severity=Severity.HIGH,
    mitre_technique="T1098 Account Manipulation",
    default_score=60, default_response="escalate",
    description="HTTP call to a privileged endpoint (password-reset, IAM mutation, role grant).",
))

# ───── DEFENSE EVASION (TA0005) ────────────────────────────────────────────
# Sprint 2 2026-06-15. Anti-tamper for Aegis's own control plane.
# Any write attempt against the tables Aegis depends on for enforcement is
# adversarial intent — the only legitimate reason an *agent* would mutate
# audit_logs / policies / kill_switches is to disable the rule that's about
# to catch its next call. Tier=deny; DDL variant=quarantine.
_register(SignalDefinition(
    id="aegis_control_plane_write",
    objective=SecurityObjective.DEFENSE_EVASION,
    severity=Severity.CRITICAL,
    mitre_technique="T1070.002 Indicator Removal: Clear Linux or Mac System Logs",
    default_score=95, default_response="deny",
    description=(
        "DML (INSERT/UPDATE/DELETE) against an Aegis control-plane table "
        "(audit_logs, policies, kill_switches, decisions, transparency_roots, "
        "incidents, human_override_events, api_keys, permissions, agents). "
        "Tamper attempt — the agent surface has no legitimate reason to "
        "mutate Aegis's own enforcement state."
    ),
))
_register(SignalDefinition(
    id="aegis_control_plane_destructive_ddl",
    objective=SecurityObjective.DEFENSE_EVASION,
    severity=Severity.CRITICAL,
    mitre_technique="T1485 Data Destruction",
    default_score=100, default_response="quarantine",
    description=(
        "DDL (DROP/TRUNCATE/ALTER) against an Aegis control-plane table. "
        "Unambiguous attack: stronger than the generic destructive_sql_ddl "
        "signal because it explicitly targets Aegis's own forensic + "
        "enforcement substrate. Always quarantines the agent."
    ),
))

# ───── CREDENTIAL ACCESS (TA0006) ──────────────────────────────────────────
_register(SignalDefinition(
    id="cloud_credential_path",
    objective=SecurityObjective.CREDENTIAL_ACCESS,
    severity=Severity.CRITICAL,
    mitre_technique="T1552.001 Credentials In Files",
    default_score=95, default_response="deny",
    description="Read of cloud-credential file (AWS / GCP / Azure / kubeconfig / docker config).",
))
_register(SignalDefinition(
    id="ssh_credential_path",
    objective=SecurityObjective.CREDENTIAL_ACCESS,
    severity=Severity.CRITICAL,
    mitre_technique="T1552.004 Private Keys",
    default_score=95, default_response="deny",
    description="Read of SSH private key (id_rsa, id_ed25519, authorized_keys).",
))
_register(SignalDefinition(
    id="system_sensitive_path",
    objective=SecurityObjective.CREDENTIAL_ACCESS,
    severity=Severity.CRITICAL,
    mitre_technique="T1552.001 Credentials In Files",
    default_score=95, default_response="deny",
    description="Read of /etc/passwd, /etc/shadow, /proc/self, /etc/aegis or similar system-sensitive path.",
))

# ───── DISCOVERY (TA0007) ──────────────────────────────────────────────────
_register(SignalDefinition(
    id="schema_recon",
    objective=SecurityObjective.DISCOVERY,
    severity=Severity.LOW,
    mitre_technique="T1087 Account Discovery",
    default_score=10, default_response="monitor",
    description="Query against information_schema / pg_catalog / sqlite_master / sys.tables.",
))
_register(SignalDefinition(
    id="external_get",
    objective=SecurityObjective.DISCOVERY,
    severity=Severity.LOW,
    mitre_technique="T1133 External Remote Services",
    default_score=5, default_response="monitor",
    description="HTTP GET to an external (non-internal-TLD) host. Informational baseline.",
))
_register(SignalDefinition(
    id="behavior_baseline_drift",
    objective=SecurityObjective.DISCOVERY,
    severity=Severity.MEDIUM,
    mitre_technique="T1078 Valid Accounts (anomalous use)",
    default_score=30, default_response="escalate",
    description="Per-agent rolling baseline deviation: unusual tool / hour / table / 3σ burst.",
))

# ───── COLLECTION (TA0009) ─────────────────────────────────────────────────
_register(SignalDefinition(
    id="bulk_pii_egress_above_threshold",
    objective=SecurityObjective.COLLECTION,
    severity=Severity.HIGH,
    mitre_technique="T1213 Data from Information Repositories",
    default_score=50, default_response="escalate",
    description="Bulk PII read above per-call row threshold (per-risk-level).",
))
_register(SignalDefinition(
    id="bulk_pii_egress_dump",
    objective=SecurityObjective.COLLECTION,
    severity=Severity.CRITICAL,
    mitre_technique="T1213 Data from Information Repositories",
    default_score=95, default_response="deny",
    description="Bulk PII read at dump scale (≥10K rows of PII-shaped columns).",
))
_register(SignalDefinition(
    id="compression_for_exfil",
    objective=SecurityObjective.COLLECTION,
    severity=Severity.MEDIUM,
    mitre_technique="T1560 Archive Collected Data",
    default_score=35, default_response="monitor",
    description="Compression command targeting PII-shaped path or alongside known-exfil host.",
))
_register(SignalDefinition(
    id="compression_observed",
    objective=SecurityObjective.COLLECTION,
    severity=Severity.LOW,
    mitre_technique="T1560 Archive Collected Data",
    default_score=20, default_response="monitor",
    description="Compression command observed (tar/gzip/zip). Informational on its own.",
))

# ───── EXFILTRATION (TA0010) ───────────────────────────────────────────────
_register(SignalDefinition(
    id="external_pii_exfil",
    objective=SecurityObjective.EXFILTRATION,
    severity=Severity.CRITICAL,
    mitre_technique="T1567.002 Exfiltration to Web Service",
    default_score=95, default_response="deny",
    description="External POST with PII-shaped body to known-exfil destination or personal-email gateway.",
))
_register(SignalDefinition(
    id="external_post_pii_unknown_dest",
    objective=SecurityObjective.EXFILTRATION,
    severity=Severity.HIGH,
    mitre_technique="T1567 Exfiltration Over Web Service",
    default_score=60, default_response="escalate",
    description="External POST with PII-shaped body to a host not on the known-exfil allowlist.",
))
_register(SignalDefinition(
    id="known_exfil_destination",
    objective=SecurityObjective.EXFILTRATION,
    severity=Severity.HIGH,
    mitre_technique="T1567.002 Exfiltration to Web Service",
    default_score=80, default_response="deny",
    description="Request target host is on the curated exfil-destination list (transfer.sh, pastebin, …).",
))
_register(SignalDefinition(
    id="known_exfil_destination_hit",
    objective=SecurityObjective.EXFILTRATION,
    severity=Severity.LOW,
    mitre_technique="T1567.002 Exfiltration to Web Service",
    default_score=5, default_response="monitor",
    description="Hit on known-exfil-destination allowlist — informational variant.",
))
_register(SignalDefinition(
    id="slow_exfil_cumulative_threshold_breached",
    objective=SecurityObjective.EXFILTRATION,
    severity=Severity.HIGH,
    mitre_technique="T1029 Scheduled Transfer",
    default_score=45, default_response="escalate",
    description="Rolling-hour cumulative PII rows above the per-call threshold (low-and-slow pattern).",
))
_register(SignalDefinition(
    id="long_window_cumulative_breach",
    objective=SecurityObjective.EXFILTRATION,
    severity=Severity.HIGH,
    mitre_technique="T1029 Scheduled Transfer",
    default_score=70, default_response="deny",
    description="7-day rolling agent risk total crossed the long-window deny line.",
))
_register(SignalDefinition(
    id="attack_chain_match",
    objective=SecurityObjective.EXFILTRATION,
    severity=Severity.CRITICAL,
    mitre_technique="T1020 Automated Exfiltration",
    default_score=100, default_response="quarantine",
    description="Session matched a known attack-chain pattern (recon→pii→compress→external_post or cred_theft).",
))
_register(SignalDefinition(
    id="cross_agent_kill_chain",
    objective=SecurityObjective.EXFILTRATION,
    severity=Severity.CRITICAL,
    mitre_technique="T1020 Automated Exfiltration",
    default_score=100, default_response="quarantine",
    description="Tenant-wide kill chain across ≥2 distinct agents completing on an exfil step.",
))

# ───── IMPACT (TA0040) ─────────────────────────────────────────────────────
_register(SignalDefinition(
    id="destructive_sql_ddl",
    objective=SecurityObjective.IMPACT,
    severity=Severity.CRITICAL,
    mitre_technique="T1485 Data Destruction",
    default_score=95, default_response="deny",
    description="DROP TABLE / TRUNCATE / ALTER TABLE DROP.",
))
_register(SignalDefinition(
    id="destructive_sql_dml_no_predicate",
    objective=SecurityObjective.IMPACT,
    severity=Severity.CRITICAL,
    mitre_technique="T1565.001 Stored Data Manipulation",
    default_score=90, default_response="deny",
    description="DELETE / UPDATE without WHERE predicate or with tautology (WHERE 1=1).",
))
_register(SignalDefinition(
    id="destructive_shell_command",
    objective=SecurityObjective.IMPACT,
    severity=Severity.CRITICAL,
    mitre_technique="T1485 Data Destruction",
    default_score=95, default_response="deny",
    description="rm -rf, dd of=/dev, mkfs, fork-bomb, sudo-rooted shell, kubectl drain / scale=0.",
))
_register(SignalDefinition(
    id="k8s_destruction_prod",
    objective=SecurityObjective.IMPACT,
    severity=Severity.CRITICAL,
    mitre_technique="T1485 Data Destruction",
    default_score=90, default_response="deny",
    description="kubectl delete / drain on a production-class namespace.",
))
_register(SignalDefinition(
    id="k8s_destruction",
    objective=SecurityObjective.IMPACT,
    severity=Severity.HIGH,
    mitre_technique="T1485 Data Destruction",
    default_score=55, default_response="escalate",
    description="kubectl delete / drain on a non-production namespace.",
))
_register(SignalDefinition(
    id="k8s_prod_namespace_destruction",
    objective=SecurityObjective.IMPACT,
    severity=Severity.HIGH,
    mitre_technique="T1485 Data Destruction",
    default_score=60, default_response="escalate",
    description="kubectl operation matching prod-namespace markers (substring fallback).",
))
_register(SignalDefinition(
    id="iac_destruction_prod",
    objective=SecurityObjective.IMPACT,
    severity=Severity.CRITICAL,
    mitre_technique="T1485 Data Destruction",
    default_score=90, default_response="deny",
    description="terraform / pulumi / cdk destroy on a production-tagged path.",
))
_register(SignalDefinition(
    id="iac_destruction",
    objective=SecurityObjective.IMPACT,
    severity=Severity.HIGH,
    mitre_technique="T1485 Data Destruction",
    default_score=55, default_response="escalate",
    description="terraform / pulumi / cdk destroy verb (non-prod path).",
))
_register(SignalDefinition(
    id="iac_destruction_command",
    objective=SecurityObjective.IMPACT,
    severity=Severity.HIGH,
    mitre_technique="T1485 Data Destruction",
    default_score=55, default_response="escalate",
    description="Shell command containing a recognised IaC destroy verb without prod marker.",
))
_register(SignalDefinition(
    id="money_transfer_above_hard_cap",
    objective=SecurityObjective.IMPACT,
    severity=Severity.CRITICAL,
    mitre_technique="T1657 Financial Theft",
    default_score=95, default_response="deny",
    description="Wire / payment ≥ $10M (configurable hard cap).",
))
_register(SignalDefinition(
    id="money_transfer_external",
    objective=SecurityObjective.IMPACT,
    severity=Severity.HIGH,
    mitre_technique="T1657 Financial Theft",
    default_score=50, default_response="escalate",
    description="Wire ≥ $100K to external / offshore / unknown destination.",
))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get(signal_id: str) -> SignalDefinition | None:
    """Look up a signal definition by id. Returns None if not registered."""
    return _REGISTRY.get(signal_id)


def all_signals() -> Iterable[SignalDefinition]:
    """Iterate every registered signal. Order matches insertion (objective groups)."""
    return _REGISTRY.values()


def by_objective(objective: SecurityObjective) -> list[SignalDefinition]:
    """All signals serving the given MITRE-aligned objective."""
    return [s for s in _REGISTRY.values() if s.objective == objective]


def by_mitre_technique(technique_id: str) -> list[SignalDefinition]:
    """All signals tagged with the given MITRE technique id (e.g. 'T1485').

    Accepts both full label ('T1485 Data Destruction') and bare id ('T1485').
    """
    bare = technique_id.split(" ", 1)[0]
    return [s for s in _REGISTRY.values()
            if s.mitre_technique_id == bare]


def score_for_finding(finding: str) -> int:
    """Inherent score for a finding name. Tolerates attack_chain:<name>
    suffixes and the legacy '__escalate' marker.

    Used by the cumulative risk pipeline (risk_pipeline.py) so it doesn't
    have to keep its own copy of the score table.
    """
    if not finding:
        return 0
    base = finding.replace("__escalate", "")
    if base.startswith("attack_chain:"):
        # All attack-chain matches roll up to attack_chain_match in the registry.
        sig = _REGISTRY.get("attack_chain_match")
        return sig.default_score if sig else 100
    sig = _REGISTRY.get(base)
    return sig.default_score if sig else 0


def mitre_for_finding(finding: str) -> dict[str, str]:
    """Return {"tactic": "TA00xx", "technique": "T1xxx[.xxx] Name"} for the
    finding name. Empty dict if not registered. Surfaced into the /execute
    response so SOC tooling can map findings into MITRE without a join."""
    if not finding:
        return {}
    base = finding.replace("__escalate", "")
    if base.startswith("attack_chain:"):
        base = "attack_chain_match"
    sig = _REGISTRY.get(base)
    if not sig:
        return {}
    return {
        "tactic":     sig.mitre_tactic,
        "technique":  sig.mitre_technique,
        "objective":  sig.objective.value,
        "severity":   sig.severity.value,
    }


def objective_for_finding(finding: str) -> SecurityObjective | None:
    """The MITRE-aligned objective a finding serves. None if unregistered."""
    if not finding:
        return None
    base = finding.replace("__escalate", "")
    if base.startswith("attack_chain:"):
        base = "attack_chain_match"
    sig = _REGISTRY.get(base)
    return sig.objective if sig else None


def default_response_for(finding: str) -> str:
    """Inherent tier for a finding ('monitor'/'escalate'/'deny'/'quarantine').

    Empty string when the finding name isn't registered — caller decides
    fall-through behaviour.
    """
    if not finding:
        return ""
    base = finding.replace("__escalate", "")
    if base.startswith("attack_chain:"):
        base = "attack_chain_match"
    sig = _REGISTRY.get(base)
    return sig.default_response if sig else ""


def registered_signal_names() -> set[str]:
    """Convenience: every registered signal id, for drift assertions in tests."""
    return set(_REGISTRY.keys())
