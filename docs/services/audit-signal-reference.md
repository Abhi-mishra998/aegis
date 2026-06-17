# Audit Signal Reference

*The authoritative list of canonical signal ids Aegis emits. Every deny row carries one of these strings as `metadata.findings[0]`. Every Threat Graph cell, every per-agent MITRE coverage row, every SIEM event, every `/audit/agent-findings/{id}` bucket keys off this vocabulary.*

This page is mirrored from `services/security/signal_registry.py` — the registry is the source of truth at runtime. If a row appears here that the codebase does not register, or a registered signal does not appear here, the parity is broken and the platform's MITRE coverage will silently drift. Treat this page as documentation of the registry, not as a parallel definition.

The registry holds 36 signals, grouped by [MITRE ATT&CK tactic](https://attack.mitre.org/tactics/enterprise/). Each row carries:

- `id` — the stable string the canonical model puts into `signal_findings`, the response body, and every audit row's `metadata.findings` array.
- `severity` — one of `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`.
- `MITRE tactic` — the `TAxxxx` tactic id.
- `MITRE technique` — the `T1xxx[.xxx]` technique id with its human label.
- `default_score` — inherent risk on 0–100, fed into the cumulative risk pipeline at `services/security/risk_pipeline.py`.
- `default_response` — what the engine does when this signal fires in isolation: `monitor` / `escalate` / `deny` / `quarantine`.
- `description` — one-line explanation surfaced into the `/execute` response so the SOC analyst sees *why*.

The closed set of `default_response` values matches the policy engine's tier vocabulary. The closed set of `severity` values matches the canonical Severity enum. The closed set of `MITRE tactic` values is the nine MITRE-aligned `SecurityObjective`s declared in the registry (`INITIAL_ACCESS`, `PERSISTENCE`, `PRIVILEGE_ESCALATION`, `DEFENSE_EVASION`, `CREDENTIAL_ACCESS`, `DISCOVERY`, `COLLECTION`, `EXFILTRATION`, `IMPACT`).

## Initial Access (TA0001)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `sql_injection_detected` | CRITICAL | TA0001 | T1190 Exploit Public-Facing Application | 95 | deny | Query carries SQL injection payload (UNION, stacked DROP, tautology, comment evasion). |

## Persistence (TA0003)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `credential_artifact_write` | HIGH | TA0003 | T1098.004 SSH Authorized Keys | 75 | deny | Shell or file_write that drops a credential-shaped artefact (.creds, authorized_keys, id_rsa). |

## Privilege Escalation (TA0004)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `privilege_escalation_attempt` | CRITICAL | TA0004 | T1098.001 Additional Cloud Credentials | 95 | deny | Identity-table write that elevates a principal to admin / superuser / root. |
| `identity_table_write` | HIGH | TA0004 | T1136.001 Create Account: Local Account | 60 | escalate | INSERT / UPDATE / DELETE / GRANT against users / roles / accounts / permissions tables. |
| `privilege_url_access` | HIGH | TA0004 | T1098 Account Manipulation | 60 | escalate | HTTP call to a privileged endpoint (password-reset, IAM mutation, role grant). |

## Defense Evasion (TA0005)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `aegis_control_plane_write` | CRITICAL | TA0005 | T1070.002 Indicator Removal: Clear Linux or Mac System Logs | 95 | deny | DML (INSERT/UPDATE/DELETE) against an Aegis control-plane table (audit_logs, policies, kill_switches, decisions, transparency_roots, incidents, human_override_events, api_keys, permissions, agents). Tamper attempt — the agent surface has no legitimate reason to mutate Aegis's own enforcement state. |
| `aegis_control_plane_destructive_ddl` | CRITICAL | TA0005 | T1485 Data Destruction | 100 | quarantine | DDL (DROP/TRUNCATE/ALTER) against an Aegis control-plane table. Unambiguous attack: stronger than the generic `destructive_sql_ddl` signal because it explicitly targets Aegis's own forensic + enforcement substrate. Always quarantines the agent. |

## Credential Access (TA0006)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `cloud_credential_path` | CRITICAL | TA0006 | T1552.001 Credentials In Files | 95 | deny | Read of cloud-credential file (AWS / GCP / Azure / kubeconfig / docker config). |
| `ssh_credential_path` | CRITICAL | TA0006 | T1552.004 Private Keys | 95 | deny | Read of SSH private key (id_rsa, id_ed25519, authorized_keys). |
| `system_sensitive_path` | CRITICAL | TA0006 | T1552.001 Credentials In Files | 95 | deny | Read of /etc/passwd, /etc/shadow, /proc/self, /etc/aegis or similar system-sensitive path. |

## Discovery (TA0007)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `schema_recon` | LOW | TA0007 | T1087 Account Discovery | 10 | monitor | Query against information_schema / pg_catalog / sqlite_master / sys.tables. |
| `external_get` | LOW | TA0007 | T1133 External Remote Services | 5 | monitor | HTTP GET to an external (non-internal-TLD) host. Informational baseline. |
| `behavior_baseline_drift` | MEDIUM | TA0007 | T1078 Valid Accounts (anomalous use) | 30 | escalate | Per-agent rolling baseline deviation: unusual tool / hour / table / 3σ burst. |

## Collection (TA0009)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `bulk_pii_egress_above_threshold` | HIGH | TA0009 | T1213 Data from Information Repositories | 50 | escalate | Bulk PII read above per-call row threshold (per-risk-level). |
| `bulk_pii_egress_dump` | CRITICAL | TA0009 | T1213 Data from Information Repositories | 95 | deny | Bulk PII read at dump scale (≥10K rows of PII-shaped columns). |
| `compression_for_exfil` | MEDIUM | TA0009 | T1560 Archive Collected Data | 35 | monitor | Compression command targeting PII-shaped path or alongside known-exfil host. |
| `compression_observed` | LOW | TA0009 | T1560 Archive Collected Data | 20 | monitor | Compression command observed (tar/gzip/zip). Informational on its own. |

## Exfiltration (TA0010)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `external_pii_exfil` | CRITICAL | TA0010 | T1567.002 Exfiltration to Web Service | 95 | deny | External POST with PII-shaped body to known-exfil destination or personal-email gateway. |
| `external_post_pii_unknown_dest` | HIGH | TA0010 | T1567 Exfiltration Over Web Service | 60 | escalate | External POST with PII-shaped body to a host not on the known-exfil allowlist. |
| `known_exfil_destination` | HIGH | TA0010 | T1567.002 Exfiltration to Web Service | 80 | deny | Request target host is on the curated exfil-destination list (transfer.sh, pastebin, …). |
| `known_exfil_destination_hit` | LOW | TA0010 | T1567.002 Exfiltration to Web Service | 5 | monitor | Hit on known-exfil-destination allowlist — informational variant. |
| `slow_exfil_cumulative_threshold_breached` | HIGH | TA0010 | T1029 Scheduled Transfer | 45 | escalate | Rolling-hour cumulative PII rows above the per-call threshold (low-and-slow pattern). |
| `long_window_cumulative_breach` | HIGH | TA0010 | T1029 Scheduled Transfer | 70 | deny | 7-day rolling agent risk total crossed the long-window deny line. |
| `attack_chain_match` | CRITICAL | TA0010 | T1020 Automated Exfiltration | 100 | quarantine | Session matched a known attack-chain pattern (recon→pii→compress→external_post or cred_theft). |
| `cross_agent_kill_chain` | CRITICAL | TA0010 | T1020 Automated Exfiltration | 100 | quarantine | Tenant-wide kill chain across ≥2 distinct agents completing on an exfil step. |

## Impact (TA0040)

| id | severity | MITRE tactic | MITRE technique | default_score | default_response | description |
|---|---|---|---|---|---|---|
| `destructive_sql_ddl` | CRITICAL | TA0040 | T1485 Data Destruction | 95 | deny | DROP TABLE / TRUNCATE / ALTER TABLE DROP. |
| `destructive_sql_dml_no_predicate` | CRITICAL | TA0040 | T1565.001 Stored Data Manipulation | 90 | deny | DELETE / UPDATE without WHERE predicate or with tautology (WHERE 1=1). |
| `destructive_shell_command` | CRITICAL | TA0040 | T1485 Data Destruction | 95 | deny | rm -rf, dd of=/dev, mkfs, fork-bomb, sudo-rooted shell, kubectl drain / scale=0. |
| `k8s_destruction_prod` | CRITICAL | TA0040 | T1485 Data Destruction | 90 | deny | kubectl delete / drain on a production-class namespace. |
| `k8s_destruction` | HIGH | TA0040 | T1485 Data Destruction | 55 | escalate | kubectl delete / drain on a non-production namespace. |
| `k8s_prod_namespace_destruction` | HIGH | TA0040 | T1485 Data Destruction | 60 | escalate | kubectl operation matching prod-namespace markers (substring fallback). |
| `iac_destruction_prod` | CRITICAL | TA0040 | T1485 Data Destruction | 90 | deny | terraform / pulumi / cdk destroy on a production-tagged path. |
| `iac_destruction` | HIGH | TA0040 | T1485 Data Destruction | 55 | escalate | terraform / pulumi / cdk destroy verb (non-prod path). |
| `iac_destruction_command` | HIGH | TA0040 | T1485 Data Destruction | 55 | escalate | Shell command containing a recognised IaC destroy verb without prod marker. |
| `money_transfer_above_hard_cap` | CRITICAL | TA0040 | T1657 Financial Theft | 95 | deny | Wire / payment ≥ $10M (configurable hard cap). |
| `money_transfer_external` | HIGH | TA0040 | T1657 Financial Theft | 50 | escalate | Wire ≥ $200K to external / offshore / unknown destination. |

## How signals flow

Every signal listed above flows through the same pipeline:

1. **Detection.** The canonical engine at `services/policy/canonical.py` (slow path) and the rego policy at `policy/agent_policy.rego` (fast path) both emit signals from this registry. The gateway's pre-policy hard-deny chain at `services/gateway/middleware.py` emits a strict subset (`system_sensitive_path`, `cloud_credential_path`, `ssh_credential_path`, `path_traversal_detected`, `sql_injection_detected`, `k8s_destruction_prod`, `iac_destruction_prod`, `money_transfer_above_hard_cap`) directly without consulting the engines.
2. **Tier resolution.** The cumulative risk pipeline at `services/security/risk_pipeline.py` reads `default_score` from this registry, sums per-session and per-agent windows, and picks the active tier (0 / 20 / 40 / 70 / 95).
3. **Response shaping.** The `/execute` response body returns `findings: [<signal_id>, …]`, `mitre: {tactic, technique, objective, severity}`, `risk_score`, `policy_id`, `explanation`. See [Cryptographic Audit Chain](../security/crypto-audit-chain.md) for what then lands in the audit row.
4. **Audit persistence.** The gateway's `_log_audit` writes `metadata.findings = [<signal_id>]` on every deny row (auto-injected from the `reason` field if the caller did not set it explicitly — see [Audit service: Findings on deny rows](audit.md#findings-on-deny-rows-canonical-signal-id-per-row)). The trigger from Alembic revision `3a519b48a6f2` then enforces append-only at the database layer.
5. **Aggregator coverage.** `/audit/agent-findings/{id}`, `/audit/top-findings`, `/audit/finding-breakdown`, the per-agent MITRE matrix on the Threat Graph, and every SIEM forwarder all key off `metadata_json->'findings'`. The registry-driven invariant is what makes those endpoints non-empty for blocked agents.

## How to add a new signal

1. Add an entry to `services/security/signal_registry.py` in the matching `SecurityObjective` block. Keep within the file's alphabetised convention.
2. Update this page with the new row.
3. The drift test in `tests/test_signal_registry_parity.py` (or equivalent) asserts that every signal name the canonical engine actually emits is registered here; CI catches drift the moment a new emit path lands without a registry entry.
4. No other file in the codebase should hold a signal-name → score / tier mapping.

## Related

- [Audit service](audit.md) — how deny rows are written, signed, chained, and queried
- [Cryptographic Audit Chain](../security/crypto-audit-chain.md) — the signing math and the offline verifier
- [Detection Pipeline](../security/detection-pipeline.md) — where the canonical engine sits in the request lifecycle
- [Threat Scenarios](../security/threat-scenarios.md) — adversarial cases the registry was designed against
