"""
Canonical Action Model — single source of truth for "what is the agent
actually trying to do?", regardless of which tool name or argument shape
the SDK happened to use.

ARCH-1 2026-06-15. Closes the entire class of "rule reads x at path A,
gateway extracts x at path B" bugs by making every action-semantics rule
read ONLY from `canonical.<field>` instead of `raw_args.<field>`.

Before:
  tool=http_request body={amount:250000}      → amount missed
  tool=wire_transfer amount_usd=250000        → amount caught
  rule reads .arguments.amount_usd, gateway puts at .amount → silent miss

After:
  normalize("http_request", {"body":{"amount":250000,"recipient":"…offshore…"}})
    → CanonicalAction(action_type="money_transfer", amount_usd=250000,
                      destination_kind="offshore", recipient="…")
  rule reads canonical.amount_usd → always finds it.

The normalizer is a PURE FUNCTION. No I/O, no Redis. Easy to unit-test
and easy to call from both the gateway middleware (fast path) and any
policy port that wants to evaluate without re-extracting.
"""
from __future__ import annotations

import re
import urllib.parse
from typing import Any, Literal, TypedDict


# ---------------------------------------------------------------------------
# Canonical action type vocabulary.
# Closed enum — rules switch on these.
# ---------------------------------------------------------------------------
ActionType = Literal[
    "money_transfer",
    "db_query",
    "db_write",
    "file_read",
    "file_write",
    "shell_command",
    "k8s_op",
    "iac_op",
    "external_post",
    "external_get",
    "code_exec",
    "unknown",
]

DestinationKind = Literal["internal", "external", "offshore", "unknown"]


class CanonicalAction(TypedDict, total=False):
    """Normalized representation of an agent tool call.

    Every field is optional. Rules MUST treat missing keys as "not present"
    rather than "false" / "0".
    """

    # WHAT type of action — the rule switches on this first.
    action_type: ActionType

    # MONEY MOVEMENT
    amount_usd: int
    recipient: str
    destination_kind: DestinationKind

    # DB
    rows_requested: int
    table_norm: str
    query_norm: str
    schema_recon: bool
    is_destructive_ddl: bool
    is_destructive_dml_no_predicate: bool
    contains_pii_columns: bool

    # FILE
    file_path: str
    sensitive_path: bool   # /etc/passwd, /etc/shadow, /proc/self
    cred_path: bool        # /root/.aws, /root/.ssh, /etc/kubernetes/admin.conf

    # SHELL / COMMAND
    command: str
    command_norm: str
    is_destructive_shell: bool
    is_compression: bool

    # K8s
    k8s_namespace: str
    k8s_verb: str
    k8s_targets_prod: bool

    # IaC
    iac_tool: str
    iac_action: str
    iac_targets_prod: bool

    # HTTP / external
    url: str
    url_host: str
    is_external_url: bool
    is_known_exfil_dest: bool

    # Generic
    raw_norm: str         # whitespace-collapsed, lowercased blob for grep rules

    # Signals + inherent risk for this single action (no session accumulation).
    signal_findings: list[str]
    risk_score_inherent: int  # 0..100


# ---------------------------------------------------------------------------
# Vocabularies (mirrors local_action_semantics.py constants).
# Keeping them here so canonical.py is self-contained and can be imported
# from gateway without dragging in policy deps.
# ---------------------------------------------------------------------------
_SHELL_DESTRUCT_PATTERNS = (
    "rm -rf", "rm -fr", "rm --recursive",
    "dd of=/dev", "mkfs",
    ":(){:|:&};:",
    "chmod -r 777 /", "chown -r /",
    "shutdown -h", "reboot", "halt", "init 0",
    "kubectl drain", "kubectl scale --replicas=0",
    "dropdb", "pg_drop",
    # Priv-esc shell entry — FUP-2 2026-06-15. `sudo` followed by a shell
    # or root spawn = the agent is escaping its sandbox. Match the common
    # incantations a real attacker / compromised tool would emit.
    "sudo -i", "sudo -s", "sudo su", "sudo bash", "sudo sh",
    "sudo /bin/bash", "sudo /bin/sh", "sudo /usr/bin/bash",
    "sudo -u root /bin/bash", "sudo -u root /bin/sh",
    "sudo -u root bash", "sudo -u root sh",
    "su -", "su root", "su - root",
    "pkexec", "doas ",
)
_SHELL_PIPE_TARGETS = ("| sh", "| bash", "| zsh", "|sh ", "|bash ", "|zsh ")
_COMPRESSION_PATTERNS = (
    "tar czf", "tar -czf", "tar zcvf", "tar -zcvf",
    "tar cjf", "tar -cjf",
    "gzip ", "zip -r", "7z a", "xz -",
)

_IAC_DESTROY_VERBS = (
    "destroy", "down", "delete-stack", "delete_stack",
    "remove-stack", "cleanup",
)
_IAC_TOOLS = (
    "terraform", "pulumi", "cdk", "aws cloudformation",
    "gcloud deployment-manager", "az deployment",
)
_IAC_PROD_MARKERS = (
    "/opt/prod-infra", "prod-infra", "/etc/prod", "production",
)

_K8S_DESTRUCT_VERBS_REGEX = re.compile(
    r"\b(delete|drain|scale|destroy)\b", re.IGNORECASE
)
_K8S_PROD_NS_TOKENS = (
    "prod", "production", "prd", "live", "main", "master",
)

_SENSITIVE_PATHS = (
    "/etc/passwd", "/etc/shadow", "/etc/gshadow",
    "/etc/sudoers", "/etc/group",
    "/etc/aegis", "/etc/secrets", "/etc/pki", "/etc/ssl/private",
    "/proc/self/", "/proc/1/", "/proc/kmsg", "/proc/kallsyms",
    "/sys/firmware",
)
_CRED_PATHS = (
    "/root/.aws", "/root/.ssh", "/root/.docker",
    "/home/.aws", "/home/.ssh",
    "/etc/kubernetes/admin.conf",
    "id_rsa", "id_ed25519", ".kube/config",
    ".npmrc", ".pypirc", ".dockercfg",
    "credentials",
)

_PII_COLUMN_TOKENS = (
    "ssn", "social_security", "passport", "credit_card", "ccn", "cvv",
    "dob", "date_of_birth", "drivers_license", "tax_id", "ein",
    "patient_id", "medical_record", "diagnosis",
)
_PII_TABLES = (
    "customer", "customers", "user", "users",
    "account", "accounts", "patient", "patients",
    "applicant", "applicants", "subscriber", "subscribers",
)

# GAP-1 2026-06-15 — identity / privilege tables. Any write to these is
# treated as privilege manipulation regardless of column shape.
_IDENTITY_TABLES = (
    "users", "user", "accounts", "account", "admins", "admin",
    "roles", "role", "permissions", "permission",
    "groups", "group", "memberships", "membership",
    "api_keys", "api_key", "tokens", "token",
    "passwords", "password", "credentials", "credential",
    "service_accounts", "service_account",
)

# Sprint 2 2026-06-15 — Aegis control-plane tables. Any agent-issued write
# (DML or DDL) against these = tamper attempt. Tier deny / quarantine.
#
# Scope rule: include ONLY tables that have NO legitimate agent write.
# Tables that overlap with the identity surface (`agents`, `api_keys`,
# `permissions`) are intentionally EXCLUDED — they're already covered by
# the `identity_table_write` / `privilege_escalation_attempt` rules
# (which DO allow benign updates like `last_login=now()` on the agents
# table while still escalating role-elevation).
#
# Kept here as a single source of truth so the rule in evaluate_full
# doesn't go re-listing names. Sprint 3 may move it under
# services/security/objectives/defense_evasion.py — until then this is the
# only place to grow the list.
_AEGIS_CONTROL_PLANE_TABLES = (
    # Forensic substrate (audit chain). Read-only from agent surface;
    # writes are forensic tampering.
    "audit_logs", "audit_log",
    "transparency_roots", "transparency_root",
    "transparency_historical_keys",
    "decisions", "decision",
    "audit_notes",
    # Enforcement state. Mutating these = disable the rule that catches
    # the next call.
    "policies", "policy",
    "policy_versions",
    "kill_switches", "kill_switch",
    # Incident / approval workflow. Mutating these = forge or hide a
    # SOC trail.
    "incidents", "incident",
    "incident_comments",
    "human_override_events", "human_override_event",
    "autonomy_violations",
    "playbook_runs",
    # Shadow + online-eval state. Agent surface never writes these.
    "shadow_policies", "shadow_policy",
    "shadow_decisions", "shadow_decision",
    "online_eval_configs", "online_eval_config",
    # Notification + scheduling internals.
    "scheduled_reports",
    "notifications",
    # Behavioural memory caches Aegis owns end-to-end.
    "behavior_baseline", "session_intelligence",
    "agent_metadata_cache",
)
_PRIVILEGE_ROLE_TOKENS = (
    "'admin'", "\"admin\"", "='admin'", "=admin,",
    "'superuser'", "\"superuser\"", "='superuser'",
    "'root'", "\"root\"", "='root'",
    "'owner'", "\"owner\"", "is_admin", "is_root",
    "is_superuser", "role:admin", "role=admin",
    "privilege:admin", "grant_all", "all privileges",
)
_PRIVILEGE_URL_PATTERNS = (
    "/users/reset-password", "/users/password-reset",
    "/admin/create", "/admin/reset",
    "/iam/users", "/iam/access-keys",
    "/auth/elevate", "/auth/sudo",
    "/account/promote", "/role/grant",
)

# Sprint 8 — the lists used to live inline. They moved to
# `services/policy/pattern_catalog.py` so the same source feeds the
# Rego generator (rego_emitter.py). The names are re-exported as
# module-level aliases for back-compatibility with any external
# import (`from services.policy.canonical import _KNOWN_EXFIL_DESTS`).
from services.policy.pattern_catalog import (  # noqa: E402
    EXFIL_HOSTS as _KNOWN_EXFIL_DESTS,
    OFFSHORE_TOKENS as _OFFSHORE_TOKENS,
)
_INTERNAL_TOKENS = (
    "internal", "acme-ops", "@apexbank.internal",
)


def _to_int(v: Any, default: int = 0) -> int:
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return int(v)
    if isinstance(v, str):
        cleaned = re.sub(r"[\$,\s]", "", v)
        try:
            return int(float(cleaned))
        except (ValueError, TypeError):
            return default
    return default


def _flatten_strings(d: Any, out: list[str]) -> None:
    """Recursively collect every string value out of a nested dict/list."""
    if isinstance(d, str):
        out.append(d)
    elif isinstance(d, dict):
        for v in d.values():
            _flatten_strings(v, out)
    elif isinstance(d, list):
        for v in d:
            _flatten_strings(v, out)


def _normalize_for_match(s: str) -> str:
    """Lowercase + collapse whitespace + drop ANSI escapes for substring grep."""
    if not isinstance(s, str):
        return ""
    s = re.sub(r"\x1b\[[0-9;]*m", "", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    return s


def _merge_all_params(raw_args: dict[str, Any]) -> dict[str, Any]:
    """Flatten input/parameters/payload/arguments wrappers into a single dict.

    Mirrors gateway middleware's `_all_params` logic so the canonical
    extractor doesn't double-strip. Caller may pass either the already-flat
    arguments dict OR the raw /execute body — both work.
    """
    if not isinstance(raw_args, dict):
        return {}
    candidates = []
    for k in ("input", "parameters", "payload", "arguments"):
        v = raw_args.get(k)
        if isinstance(v, dict):
            candidates.append(v)
    merged: dict[str, Any] = {}
    for c in candidates:
        merged.update(c)
    # Also pull the top-level keys (the args dict itself).
    for k, v in raw_args.items():
        if k in ("input", "parameters", "payload", "arguments"):
            continue
        merged.setdefault(k, v)
    return merged


# ---------------------------------------------------------------------------
# Per-domain extractors. Each returns a partial CanonicalAction dict.
# ---------------------------------------------------------------------------

def _extract_money(params: dict[str, Any], blob_lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    amount = 0
    # Search top-level + nested body.
    scopes = [params]
    if isinstance(params.get("body"), dict):
        scopes.append(params["body"])
    for scope in scopes:
        if amount:
            break
        for k in ("amount_usd", "amount", "value", "total", "settlement_amount"):
            amount = _to_int(scope.get(k), 0)
            if amount:
                break
    if not amount:
        return out
    out["amount_usd"] = amount

    # Recipient + destination kind.
    recipient = ""
    for k in ("recipient", "to", "destination", "beneficiary", "payee"):
        v = params.get(k)
        if isinstance(v, str) and v:
            recipient = v
            break
        if isinstance(params.get("body"), dict):
            v = params["body"].get(k)
            if isinstance(v, str) and v:
                recipient = v
                break
    out["recipient"] = recipient

    dest: DestinationKind = "unknown"
    # Pull URL host so the internal/offshore classification keys off the
    # actual destination, not loose substring hits anywhere in the blob.
    url_for_classify = ""
    for k in ("url", "endpoint", "uri"):
        v = params.get(k)
        if isinstance(v, str) and v:
            url_for_classify = v
            break
    url_host = ""
    m = re.match(r"https?://([^/]+)", url_for_classify or "")
    if m:
        url_host = m.group(1).lower()

    if any(t in blob_lower for t in _OFFSHORE_TOKENS):
        dest = "offshore"
    elif (
        # Tightened 2026-06-15: "internal" classification requires a real
        # internal-TLD URL host OR an internal-email recipient suffix.
        # Previously any blob substring "internal" matched, so a wire to
        # http://internal-banking-system/wire was treated as internal.
        url_host.endswith((".internal", ".local", ".corp", ".intra"))
        or any(recipient.lower().endswith(s) for s in (
            "@apexbank.internal", "@acme-ops.internal",
        ) if isinstance(recipient, str))
    ):
        dest = "internal"
    elif (recipient and ("offshore" in recipient.lower()
                         or "beneficiary-" in recipient.lower())):
        dest = "external"
    else:
        # Heuristic: any 'external' / 'partner' / 'vendor' keyword in blob,
        # OR a non-internal URL host present at all (so a wire that DOES
        # specify a URL host but no clear internal marker is treated as
        # external for risk purposes).
        if (any(t in blob_lower for t in ("external", "partner", "vendor", "supplier"))
                or (url_host and not url_host.endswith((".internal", ".local", ".corp", ".intra")))):
            dest = "external"
    out["destination_kind"] = dest
    return out


def _extract_db(params: dict[str, Any], blob_lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    query = ""
    for k in ("query", "sql", "statement", "q"):
        v = params.get(k)
        if isinstance(v, str) and v.strip():
            query = v
            break
    if not query:
        return out
    qn = _normalize_for_match(query)
    out["query_norm"] = qn

    # Destructive DDL: DROP TABLE, TRUNCATE, ALTER TABLE … DROP, DROP SCHEMA,
    # DROP DATABASE. We don't flag SELECT here — destructive intent is what
    # the evaluator gates on; reads are handled by the row-limit signal.
    is_destructive_ddl = bool(re.search(
        r"\b(drop\s+table|truncate\s+table|alter\s+table\s+\S+\s+drop|drop\s+schema|drop\s+database)\b",
        qn, re.IGNORECASE
    ))
    # SQL injection signatures — surface as a distinct finding so the
    # evaluator can deny without re-parsing.
    out["sql_injection_detected"] = bool(re.search(
        r"(;\s*(drop|truncate|delete|update|insert|alter|exec|execute)\b"
        r"|\bunion\s+(all\s+)?select\b"
        r"|\b(or|and)\s+['\"]?\s*\d+\s*['\"]?\s*=\s*['\"]?\s*\d+\s*['\"]?"
        r"|--[^\n]*\b(drop|truncate|delete|union|exec|alter)\b"
        r"|['\"];\s*(--|drop|truncate|delete|insert|alter)"
        r"|\bxp_cmdshell\b|\bsp_executesql\b)",
        qn, re.IGNORECASE
    ))
    # GAP-1 2026-06-15 — identity / privilege table write.
    # Sprint 2 2026-06-15 — extended to also detect Aegis control-plane
    # mutations (audit_logs / policies / kill_switches / …). These get
    # higher-tier signals than the bare identity_table_write.
    # INSERT/UPDATE/DELETE/GRANT against users/accounts/roles/api_keys/etc.
    is_identity_write = False
    is_cp_write = False           # control-plane DML (INSERT/UPDATE/DELETE)
    is_cp_destructive = False     # control-plane DDL (DROP/TRUNCATE/ALTER)
    write_verbs = ("insert", "update", "delete", "grant", "revoke", "alter")

    def _target_table(qn_: str) -> str | None:
        """Pull the table written-to from the query, regardless of verb.
        Returns lowercase table name or None."""
        for pat in (
            r"\binto\s+([a-zA-Z_][a-zA-Z0-9_]*)",          # INSERT INTO <t>
            r"^\s*update\s+([a-zA-Z_][a-zA-Z0-9_]*)",      # UPDATE <t>
            r"^\s*delete\s+from\s+([a-zA-Z_][a-zA-Z0-9_]*)",  # DELETE FROM <t>
            r"^\s*drop\s+table\s+(?:if\s+exists\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
            r"^\s*truncate\s+(?:table\s+)?([a-zA-Z_][a-zA-Z0-9_]*)",
            r"^\s*alter\s+table\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        ):
            m = re.search(pat, qn_, re.IGNORECASE)
            if m:
                return m.group(1).lower()
        return None

    # Detect Aegis-control-plane writes first (strongest signal).
    # DDL against a control-plane table = always destructive intent.
    if re.match(r"^\s*(drop|truncate|alter)\s+table", qn, re.IGNORECASE):
        tt = _target_table(qn)
        if tt and tt in _AEGIS_CONTROL_PLANE_TABLES:
            is_cp_destructive = True
            out["table_norm"] = tt
    # DML against a control-plane table = tamper.
    if (any(qn.startswith(v + " ") for v in write_verbs)
            or qn.startswith("merge ")):
        tt = _target_table(qn)
        if tt:
            if tt in _AEGIS_CONTROL_PLANE_TABLES:
                is_cp_write = True
                out["table_norm"] = tt
            if tt in _IDENTITY_TABLES:
                is_identity_write = True
                out["table_norm"] = tt
        # GRANT/REVOKE/ALTER USER/ROLE — no table, but identity-elevating.
        if (qn.startswith("grant ") or qn.startswith("revoke ")
                or qn.startswith("alter user") or qn.startswith("alter role")):
            is_identity_write = True

    out["is_identity_table_write"]              = is_identity_write
    out["is_aegis_control_plane_write"]          = is_cp_write
    out["is_aegis_control_plane_destructive_ddl"] = is_cp_destructive
    # Privilege escalation: identity write that elevates a role/admin/root.
    out["is_privilege_escalation"] = (
        is_identity_write
        and any(tok in qn for tok in _PRIVILEGE_ROLE_TOKENS)
    )
    is_dml_no_pred = False
    # ANY DELETE/UPDATE — then evaluate WHERE presence + tautology shape.
    dml_match = re.match(r"\s*(delete|update)\b", qn, re.IGNORECASE)
    if dml_match:
        has_where = bool(re.search(r"\bwhere\b", qn, re.IGNORECASE))
        is_tautology = bool(re.search(
            r"\bwhere\s+(1\s*=\s*1|true|0\s*=\s*0|'a'\s*=\s*'a'|2\s*>\s*1|1\s*<\s*2)\b",
            qn, re.IGNORECASE
        ))
        if not has_where or is_tautology:
            is_dml_no_pred = True

    out["is_destructive_ddl"] = is_destructive_ddl
    out["is_destructive_dml_no_predicate"] = is_dml_no_pred

    # rows_requested = explicit row_limit OR LIMIT clause
    rows = _to_int(params.get("row_limit"), 0)
    if rows == 0:
        m = re.search(r"\blimit\s+(\d+)", qn, re.IGNORECASE)
        if m:
            rows = int(m.group(1))
    out["rows_requested"] = rows

    # table_norm — the FROM table (simple parser, good enough for substring rules)
    fm = re.search(r"\bfrom\s+([a-zA-Z_][a-zA-Z0-9_]*)", qn, re.IGNORECASE)
    table_norm = fm.group(1).lower() if fm else ""
    out["table_norm"] = table_norm

    # schema_recon — querying information_schema / pg_catalog
    schema_recon = bool(re.search(
        r"\b(information_schema|pg_catalog|pg_tables|pg_attribute|sqlite_master|sys\.tables|sys\.columns)\b",
        qn, re.IGNORECASE
    ))
    out["schema_recon"] = schema_recon

    # contains_pii_columns — token in SELECT list OR table is a PII table
    pii_cols = any(t in qn for t in _PII_COLUMN_TOKENS)
    pii_table = table_norm in _PII_TABLES
    out["contains_pii_columns"] = bool(pii_cols or pii_table)
    return out


def _extract_file(params: dict[str, Any], blob_lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    path = ""
    for k in ("path", "file_path", "filename", "src", "target", "uri"):
        v = params.get(k)
        if isinstance(v, str) and v:
            path = v
            break
    if not path:
        return out
    decoded = urllib.parse.unquote(path).replace("\\", "/")
    pl = decoded.lower()
    out["file_path"] = decoded

    sensitive = any(sp.lower() in pl for sp in _SENSITIVE_PATHS) or ".." in pl
    cred = any(cp.lower() in pl for cp in _CRED_PATHS)
    out["sensitive_path"] = sensitive
    out["cred_path"] = cred
    return out


def _extract_shell(params: dict[str, Any], blob_lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cmd = ""
    for k in ("command", "cmd", "shell", "bash", "exec"):
        v = params.get(k)
        if isinstance(v, str) and v:
            cmd = v
            break
    if not cmd:
        return out
    cn = _normalize_for_match(cmd)
    out["command"] = cmd
    out["command_norm"] = cn

    is_destructive = (
        any(p in cn for p in _SHELL_DESTRUCT_PATTERNS)
        or any(p in cn for p in _SHELL_PIPE_TARGETS)
    )
    out["is_destructive_shell"] = is_destructive
    out["is_compression"] = any(p in cn for p in _COMPRESSION_PATTERNS)
    return out


def _extract_k8s(params: dict[str, Any], blob_lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cn = blob_lower
    if "kubectl" not in cn and "helm " not in cn:
        return out

    # k8s_verb
    mv = re.search(r"\bkubectl\s+(\w+)", cn)
    verb = mv.group(1).lower() if mv else ""
    if not verb and "helm" in cn:
        mvh = re.search(r"\bhelm\s+(\w+)", cn)
        if mvh:
            verb = mvh.group(1).lower()
    out["k8s_verb"] = verb

    # namespace
    ns = ""
    for pat in (
        r"kubectl\s+(?:-n|--namespace=?)\s+(\S+)",
        r"kubectl\s+delete\s+(?:ns|namespace)\s+(\S+)",
        r"kubectl\s+delete\s+\S+\s+(?:-n|--namespace=?)\s+(\S+)",
        r"helm\s+(?:uninstall|delete)\s+\S+(?:\s+-n|\s+--namespace=?)\s+(\S+)",
    ):
        m = re.search(pat, cn)
        if m:
            ns = m.group(1).strip("\"'")
            break
    out["k8s_namespace"] = ns

    # targets_prod
    nsl = ns.lower()
    targets_prod = any(
        nsl == t or nsl.startswith(t + "-") or nsl.endswith("-" + t)
        for t in _K8S_PROD_NS_TOKENS
    )
    out["k8s_targets_prod"] = bool(targets_prod)
    return out


def _extract_iac(params: dict[str, Any], blob_lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    cn = blob_lower
    tool = ""
    for t in _IAC_TOOLS:
        if t in cn:
            tool = t
            break
    if not tool:
        return out

    action = ""
    for v in _IAC_DESTROY_VERBS:
        if v in cn:
            action = v
            break

    out["iac_tool"] = tool
    out["iac_action"] = action
    out["iac_targets_prod"] = any(m in cn for m in _IAC_PROD_MARKERS)
    return out


def _extract_http(params: dict[str, Any], blob_lower: str) -> dict[str, Any]:
    out: dict[str, Any] = {}
    url = ""
    for k in ("url", "endpoint", "uri"):
        v = params.get(k)
        if isinstance(v, str) and v:
            url = v
            break
    if not url:
        return out
    out["url"] = url

    host = ""
    m = re.match(r"https?://([^/]+)", url)
    if m:
        host = m.group(1).lower()
    out["url_host"] = host

    out["is_external_url"] = bool(m and not host.endswith((".internal", ".local")))
    out["is_known_exfil_dest"] = any(d in (host or url).lower() for d in _KNOWN_EXFIL_DESTS)
    # GAP-1 2026-06-15 — privilege URL pattern (password reset, IAM mutation,
    # role grant). Surface alongside whatever HTTP intent the agent emitted.
    out["is_privilege_url"] = any(p in url.lower() for p in _PRIVILEGE_URL_PATTERNS)

    # P0-1 fix 2026-06-21 — SSRF detection. The brutal review found that
    # http.get with url=file:///etc/passwd / 169.254.169.254 / localhost
    # was returned as action=allow with risk=0.0. The fix is to classify
    # those URLs here so the initial_access detector can emit the right
    # finding (ssrf_local_file, ssrf_cloud_metadata, ssrf_internal_network)
    # and the score reduction in canonical → deny tier hits.
    url_lower = url.lower()
    out["is_ssrf_local_file"] = url_lower.startswith(("file://", "gopher://", "ftp://"))
    out["is_ssrf_cloud_metadata"] = any(
        marker in url_lower for marker in _CLOUD_METADATA_MARKERS
    )
    out["is_ssrf_internal_network"] = _host_is_internal(host) if host else False
    return out


# Cloud-metadata exfil endpoints. Every IaaS publishes one; treat any HTTP
# call to these from an agent as adversarial.
_CLOUD_METADATA_MARKERS = (
    "169.254.169.254",          # AWS / GCP / Azure / Alibaba / OpenStack
    "metadata.google.internal", # GCP-preferred hostname
    "metadata.azure.com",       # Azure variant
    "fd00:ec2::254",            # AWS IMDSv6
    "100.100.100.200",          # Alibaba Cloud
)


def _host_is_internal(host: str) -> bool:
    """True if host resolves to an RFC1918 / loopback / link-local /
    *.internal / *.local target — i.e. an SSRF pivot into private network.
    Host is already lowercased + has no scheme. May include `:port`.
    """
    if not host:
        return False
    host = host.split(":", 1)[0]  # drop :port
    if host in ("localhost", "0.0.0.0"):
        return True
    if host.endswith((".internal", ".local", ".corp", ".intra", ".lan")):
        return True
    # Numeric-IP check — RFC1918 ranges + loopback + link-local
    try:
        import ipaddress
        ip = ipaddress.ip_address(host)
        return ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Action-type classification (the WHAT)
# ---------------------------------------------------------------------------
def _classify_action_type(
    tool_name: str, params: dict[str, Any], extracted: dict[str, Any]
) -> ActionType:
    tn = (tool_name or "").lower()
    # Money first — even if tool is generic http_request, amount + recipient → money_transfer.
    if extracted.get("amount_usd"):
        return "money_transfer"
    # Tool-name hints (canonical SDK names).
    if "wire" in tn or "payment" in tn or "transfer" in tn:
        return "money_transfer"
    # IaC / K8s win over generic shell.
    if extracted.get("iac_tool"):
        return "iac_op"
    if extracted.get("k8s_verb"):
        return "k8s_op"
    if "kubectl" in tn or "helm" in tn:
        return "k8s_op"
    if "terraform" in tn or "pulumi" in tn:
        return "iac_op"
    # DB
    if extracted.get("query_norm") or "sql" in tn or "db_query" in tn:
        qn = extracted.get("query_norm") or ""
        if (extracted.get("is_destructive_ddl")
                or extracted.get("is_destructive_dml_no_predicate")
                or qn.startswith(("insert", "update", "delete", "drop", "truncate", "alter"))):
            return "db_write"
        return "db_query"
    # File
    if extracted.get("file_path"):
        # Heuristic: read_file vs write_file. Default to read if tool name hints.
        if "write" in tn or "create" in tn or "upload" in tn:
            return "file_write"
        return "file_read"
    # Shell
    if extracted.get("command"):
        return "shell_command"
    # HTTP
    if extracted.get("url"):
        method = params.get("method") or "GET"
        if isinstance(method, str) and method.upper() in ("POST", "PUT", "PATCH"):
            return "external_post"
        return "external_get"
    if "shell" in tn or "exec" in tn or "code" in tn:
        return "code_exec"
    return "unknown"


# ---------------------------------------------------------------------------
# Signal scoring — Sprint 1 2026-06-15.
# Scores now live in services/security/signal_registry.py — the SINGLE source
# of truth across canonical / risk_pipeline / decision engine. Do NOT add a
# new dict here; register the signal in signal_registry.py instead.
# ---------------------------------------------------------------------------
from services.security.signal_registry import score_for_finding as _registry_score


def _signals_from_canonical(c: dict[str, Any]) -> tuple[list[str], int]:
    """Derive (findings, inherent_risk_score) from the canonical bag.

    Sprint 3 orchestrator: delegates every detector to the per-objective
    module under services/security/objectives/. The body here is just
    union + dedup + score reduction. Each module is pure-function and
    owns the logic for ONE MITRE tactic — see that directory's README.

    Adding a new detector = register in signal_registry.py + add a rule
    in the appropriate objective module. Touch this file ONLY to add a
    new objective module to the iteration order.
    """
    from services.security.objectives import DETECTORS
    from services.security.objectives import exfiltration as _exfil

    findings: list[str] = []
    seen: set[str] = set()
    for module in DETECTORS:
        if module is _exfil:
            # The exfiltration detector needs the known-exfil hosts list
            # so it can match a host that's embedded in a shell command
            # (canonical's `is_known_exfil_dest` only checks the
            # top-level URL field). Sprint 7 will replace this argument
            # with a ThreatIntelProvider lookup; for now we pass the
            # hardcoded constant through.
            emitted = module.detect(c, _KNOWN_EXFIL_DESTS)
        else:
            emitted = module.detect(c)
        for f in emitted:
            if f and f not in seen:
                seen.add(f)
                findings.append(f)

    # MAX, not SUM. Cumulative scoring is the risk_pipeline's job (ARCH-2).
    # Score values come from the central signal registry — emitting a
    # finding name that isn't registered = score 0 (fail-explicit).
    score = max((_registry_score(f) for f in findings), default=0)
    return findings, score


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize(tool_name: str, raw_args: dict[str, Any] | None) -> dict[str, Any]:
    """Produce the canonical dict for one tool call.

    ``raw_args`` is whatever the caller has. May be the already-flat
    arguments dict, the full /execute body, or an SDK-wrapped envelope.
    """
    raw_args = raw_args or {}
    params = _merge_all_params(raw_args) if any(
        k in raw_args for k in ("input", "parameters", "payload", "arguments")
    ) else dict(raw_args)

    # Build the lowercased blob for substring rules.
    string_bag: list[str] = []
    _flatten_strings(params, string_bag)
    blob_lower = _normalize_for_match(" ".join(string_bag))

    # Per-domain extraction (each is a no-op if its domain doesn't apply).
    extracted: dict[str, Any] = {}
    extracted.update(_extract_money(params, blob_lower))
    extracted.update(_extract_db(params, blob_lower))
    extracted.update(_extract_file(params, blob_lower))
    extracted.update(_extract_shell(params, blob_lower))
    extracted.update(_extract_k8s(params, blob_lower))
    extracted.update(_extract_iac(params, blob_lower))
    extracted.update(_extract_http(params, blob_lower))

    extracted["raw_norm"] = blob_lower[:2000]
    extracted["action_type"] = _classify_action_type(tool_name, params, extracted)
    findings, score = _signals_from_canonical(extracted)
    extracted["signal_findings"] = findings
    extracted["risk_score_inherent"] = score

    return extracted


# ---------------------------------------------------------------------------
# Convenience tier mapping for callers that want the canonical tier directly.
# ---------------------------------------------------------------------------
def tier_from_score(score: int) -> str:
    """Map inherent risk to a tier name.

    Tiers (ARCH-3):
      ALLOW       0-19
      MONITOR    20-39
      ESCALATE   40-69
      DENY       70-94
      QUARANTINE 95+
    """
    if score >= 95: return "quarantine"
    if score >= 70: return "deny"
    if score >= 40: return "escalate"
    if score >= 20: return "monitor"
    return "allow"
