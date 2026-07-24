                                       # Aegis / ACP — Roadmap-21

                                       **Date:** 2026-07-21
                                       **Owner:** Aegis Platform, ByteHubble
                                       **Basis:** ATF v3.2 spec (`ATF_v3.0_Verifiable_Kernel.md`) + verified codebase state as of 2026-07-21 (see `AUDIT_2026_07_21.md`, 6 rounds, every finding file:line-grounded).
                                       **Purpose:** Map every component in the ATF v3.2 kernel to what's already built, what's partially built, and what's missing — then pipe that map through a phased action plan that also clears the outstanding audit debt. No fabricated percentages: each "% built" is the ratio of ATF-required capabilities present in code to the total required (files listed).

                                       ---

                                       ## 0. One-Page Summary

                                       The Aegis kernel that ATF v3.2 specifies is **~55% built** at the code level, but the pieces most of the value comes from — the **Execution Witness** (0%) and the ATF-canonical **Aegis Profile / identity consumption** (~30%) — are the exact pieces missing. Everything else (Capability Gate, Execution Ledger, agent state machine, escalation flow, policy compiler + shadow mode, `aegis-verify` CLI) already exists in some form and needs realignment rather than green-field construction.

                                       **The path in one sentence:** close the outstanding audit debt (Phase 0, ~2 weeks) → refactor the existing Gate and Ledger to the ATF canonical shape (Phase 1a, ~4 weeks) → build the Witness (Phase 1b, ~8 weeks — this is the wedge) → finish the kernel (Phase 1c, ~4 weeks) → Phase 1 exit. Every phase has a measurable exit gate; nothing advances on vibes.

                                       ---

                                       ## 1. Current-State Map — ATF v3.2 Component → Codebase

                                       ### 1.1 Component 1 — Capability Gate

                                       **Status: ~70% built.**

                                       | ATF requirement | Current code | Gap |
                                       |---|---|---|
                                       | Policy check BEFORE tool execution | `services/decision/main.py` fans out to policy + behavior; `services/policy/router.py` fast-path + slow-path | ✓ exists |
                                       | MCP middleware as primary interception | `services/mcp_server/` is **client-side** (drop-in for MCP-aware IDE clients); `services/gateway/routers/openai_messages.py` + `messages.py` proxy LLM calls | ✗ **no MCP-server-side proxy** — the "sit between agent runtime and MCP servers" position is unbuilt |
                                       | HTTP egress proxy (secondary) | Gateway is the HTTP proxy for LLM traffic | ✓ partial (covers LLM-shaped traffic, not arbitrary tool HTTP) |
                                       | SDK shim (tertiary) | `sdk/acp_client/`, `sdk/common/` + 4 integrations (`aegis-anthropic/bedrock/langchain/openai`) | ✓ exists |
                                       | OPA/Rego decision (AuthZEN-compatible PDP) | 6 rego files under `services/policy/policies/`, plus `services/policy/local_eval.py` fast path | ✓ exists |
                                       | Positive allow-list grant semantics | `services/policy/local_eval.py:62-67` — **verified positive allow-list** (Round 6) | ✓ |
                                       | Deterministic action classification C0/C1/C2/C3 | `services/policy/canonical.py` has 5-tier `allow/monitor/escalate/deny/quarantine` — **different taxonomy** | ✗ **realign to ATF C0-C3** predicate |
                                       | Tool manifest hash pinning (MCP-specific) | Not present; agent metadata includes tools but no tool_manifest hash in every decision | ✗ **missing** |
                                       | Policy manifest hash in every ledger entry | Partial — memory notes `policy_manifest_hash` is written; needs verification | ⚠︎ verify |
                                       | Escalation timeout → DENY (never ALLOW) | `services/autonomy/router.py` approval flow — timeout behavior needs verification | ⚠︎ verify |
                                       | Single-action approval binding + 15-min expiry | `services/gateway/proxy_helpers.py:110` — `APPROVAL_REPLAY_TTL_S = 300` (5-min replay window) — needs re-alignment with ATF's 15-min approval-expiry (§5.7) | ⚠︎ realign |
                                       | Deny-wins conflict rule (approval) | Not verified | ⚠︎ verify |
                                       | Policy testing: unit tests, replay, shadow mode, dry-run, coverage | Shadow-mode exists (memory: `services/gateway/shadow_eval_hook.py`); replay-simulation not verified; coverage report not present | ⚠︎ partial |
                                       | Delegation: subset intersection, depth ≤ 2, ≤ 1h expiry, cascade revoke | Standard OAuth token exchange (RFC 8693) — needs verification against 5 delegation rules | ⚠︎ verify |
                                       | Default-deny egress with Gate as only path | Deployment-time posture — **no install-time checker in code** | ✗ **missing** |
                                       | **P1-17** (from audit): `KILL_AGENT`/`ISOLATE_AGENT` / `REVOKE_KEY` have no rego rule; ARE zeroes risk and trusts OPA, but OPA has no matching rule | Real gap discovered while closing Open Q #11 | ✗ **fix required** |

                                       **Verdict:** Gate skeleton is solid; the ATF-canonical shape (C0-C3 taxonomy, tool manifest hashes, deny-wins, timeout-DENY, egress checker, destructive-action rego) is the delta.

                                       ---

                                       ### 1.2 Component 2 — Execution Witness

                                       **Status: 0% built. This is the wedge.**

                                       | ATF requirement | Current code | Gap |
                                       |---|---|---|
                                       | Sidecar container, separate trust from agent | Not present. `services/flight_recorder/` records timelines but writers are the **agent runtime itself** — self-reported, not independent observation | ✗ **entire component absent** |
                                       | eBPF socket tracing / TLS SNI / connection metadata | Not present | ✗ |
                                       | API tap: response codes + payload hashes | Not present. Gateway sees responses but doesn't emit them as an independent observation | ✗ |
                                       | Filesystem eBPF probes (open/write/unlink) | Not present | ✗ |
                                       | Process eBPF (exec/fork) | Not present | ✗ |
                                       | Resource counters (CPU/mem/net) | Prometheus scraped by ops, not attested by a witness | ✗ |
                                       | `CORROBORATED` / `CONTRADICTED` / `UNOBSERVED` verdict trichotomy | Not present. Current audit records the Gate's ALLOW; the "did it actually happen" side is missing | ✗ |
                                       | Witness attestation record (§6.4) | Not present | ✗ |
                                       | Heartbeat + degradation → `UNOBSERVED` | Not present | ✗ |
                                       | Automatic Gate lockout on `CONTRADICTED` | Not present | ✗ |
                                       | Witness signing key + rotation | Not present | ✗ |

                                       **Verdict:** This component is the reason ATF v3.2 exists. §1 explicitly states the differentiator IS the Witness + Ledger + Gate binding, and observation-vs-attestation is what a competent team's own eBPF + Falco + OTel stack does not do. **This is the highest-leverage build in the roadmap and it needs the most engineering weeks.**

                                       ---

                                       ### 1.3 Component 3 — Execution Ledger

                                       **Status: ~80% built.**

                                       | ATF requirement | Current code | Gap |
                                       |---|---|---|
                                       | Append-only per-tenant hash chain | `services/audit/aggregator.py`, `services/audit/models.py` — `prev_root_hash` chain | ✓ exists |
                                       | Merkle batching + signed roots | `services/audit/merkle.py`, `services/audit/signer.py` (Ed25519) — verified in Round 2 as clean, well-implemented | ✓ exists |
                                       | External anchoring | S3 transparency log `s3://aegis-public-roots-628478946931` (per memory 2026-06-14). RFC 3161 alternative not present | ✓ partial (S3 only) |
                                       | Open-source `aegis-verify` CLI | `tools/aegis_verify/verifier.py` + PyPI package `aegis-aevf` (per memory) | ✓ exists |
                                       | Export bundle for regulator (JSON-lines + Merkle proofs + policy manifests + summary) | Compliance export endpoint exists (`services/gateway/routers/compliance.py`); needs alignment with ATF §7.3 shape | ⚠︎ partial |
                                       | **RFC 8785 JCS canonicalization (§7.2 item 0)** | `services/audit/aggregator.py` uses `canonical_json` helper — needs verification it's byte-identical to RFC 8785 (JCS) or a documented deviation | ⚠︎ **verify or migrate** |
                                       | Ledger entry schema per §7.1 (intent + authorization + observation + outcome + chain) | Current audit rows have action/decision/reason/metadata_json/prev_hash — **shape does not match ATF §7.1 directly**; needs canonical restructure or a v3.0 view over the existing store | ⚠︎ **restructure or view-layer** |
                                       | Response payload hash in `outcome` (C2/C3) | Not present. Audit rows don't include a downstream response hash | ✗ **missing** |
                                       | `witness_attestation_id` binding in ledger entry | Not present (waits on Witness) | ✗ blocked on §1.2 |
                                       | `human_verification` in outcome | Partial — approval events exist in `services/autonomy/` but not stitched into the outcome slice of a single ledger entry | ⚠︎ partial |
                                       | Schema versioning per §7.4 with `schema_version` self-declared field | Not present | ✗ **add** |
                                       | Verifier refuses unknown MAJOR versions | Not present (verifier accepts all) | ✗ **add** |
                                       | Anchor cross-signing on key rotation | Memory: `scripts/maintenance/rotate_transparency_key.py` promotes current key to `transparency_historical_keys` — this covers historical verification. Cross-signing of the transition batch is not verified. | ⚠︎ verify |
                                       | Destruction certificate on decommission | Not present | ✗ **add** |
                                       | Retention floor 6 months | Enforced at store level? Memory notes retention config exists. | ⚠︎ verify |
                                       | P0-3 + P0-5 (audit): append-only trigger pending + billing_status update path conflicts | See `AUDIT_2026_07_21.md` P0-3, P0-5 | ✗ **fix required** |

                                       **Verdict:** Ledger is closer to ATF-shape than either sibling. The realignment work is (a) schema versioning, (b) restructure entries into the intent/authorization/observation/outcome quads, (c) RFC 8785 verification, (d) response payload hash, (e) close P0-3/P0-5 coupling.

                                       ---

                                       ### 1.4 Identity: Consume, Don't Compete

                                       **Status: ~30% built.**

                                       | ATF requirement | Current code | Gap |
                                       |---|---|---|
                                       | SPIFFE SVID acceptance | Not present | ✗ **add adapter** |
                                       | Entra Agent ID token acceptance | Not present | ✗ **add adapter** |
                                       | Okta for AI Agents / XAA / token exchange | Not present | ✗ **add adapter** |
                                       | OAuth 2.1 client credential + DPoP (fallback) | Legacy HS256 tokens + Clerk RS256 tokens — DPoP not present | ⚠︎ partial |
                                       | Clerk identity (own IdP wrapper) | Well-integrated — `sdk/common/clerk_auth.py`, webhook signature verify, JWKS cache | ✓ exists but different from ATF stack |
                                       | IETF AIMS composition (draft-klrc-aiagent-auth-00) | Not present | ✗ (bleeding-edge; add as adapter when the draft advances) |
                                       | Aegis Profile document (§4.3) with `subject`, `human_responsible`, `provenance`, `gate_policy_ref`, `action_class_max`, tenant signature | Not present as a first-class object. JWT claims contain some of this (`permissions`, `risk_level`) but not the full ATF profile | ✗ **add profile document** |
                                       | Provenance block (model_ref, prompt_template_hash, tool_manifest_hash, container_image_digest, sbom_ref) | Not present | ✗ **add** |
                                       | SCIM agent extension | SCIM tokens exist (`services/gateway/routers/scim.py`, `scim_tokens.py`) — token-level SCIM, not full agent-extension provisioning | ⚠︎ partial |
                                       | Tenant issuance quota (§4.4) | Not present | ✗ **add** |
                                       | `human_responsible` binding + orphan quarantine | Not present | ✗ **add** |
                                       | Ledger-visible identity birth (C2 event) | Provisioning events exist in audit but not typed as C2 birth events | ⚠︎ retype |
                                       | **P2-8** (audit): Clerk decode skips `verify_aud` | Real defense-in-depth downgrade | ✗ **fix** |
                                       | **P1-14** (audit): learning repository drops `tenant_id` filter | Real cross-tenant hazard | ✗ **fix** |

                                       **Verdict:** Identity consumption is the second-biggest gap after the Witness. Aegis today builds its OWN identity stack around Clerk; ATF v3.2 says explicitly *don't do that*. The path is to keep Clerk as one of many accepted inputs, add SPIFFE + Entra + Okta adapters, and produce the Aegis Profile as an overlay.

                                       ---

                                       ### 1.5 Agent States (No Numeric Score)

                                       **Status: ~50% built.**

                                       | ATF requirement | Current code | Gap |
                                       |---|---|---|
                                       | Four states: `VERIFIED` / `RESTRICTED` / `QUARANTINED` / `UNKNOWN` | Current `agent.status` enum is `active` / `suspended` / `quarantined` / `terminated` | ⚠︎ **taxonomy realign** |
                                       | State derivation is deterministic (contradiction ratio, unobserved ratio, escalation ratio) | Not present as a state-derivation function; risk score is still a numeric fusion | ⚠︎ partial |
                                       | State transitions ledgered | Audit rows record status changes | ✓ partial |
                                       | Deny reasons cite the rule, not a number | Current denials cite `reason` + `findings` — mostly rule-labeled (see Round 2 findings P2-5 divergence between fast/slow path) | ✓ partial |
                                       | No numeric trust score in decisions | `services/decision/engine.py` still uses fused numeric risk (cross_agent_risk, sequence_risk, velocity_risk) | ⚠︎ **rework toward states** — numeric can survive as INPUT to state derivation, but not as the OUTPUT the Gate consumes |

                                       **Verdict:** Migrate over 2-3 sprints — the numeric machinery is fine as internal signal fusion, but the Gate's authoritative input should become the state, not the score.

                                       ---

                                       ### 1.6 Escalation Flow (§5.7)

                                       **Status: ~60% built.**

                                       Autonomy service (`services/autonomy/`) has approvals + Slack webhook executor + Jira/ServiceNow adapters. Deltas vs ATF §5.7:
                                       - Ordered `approvers` list with quorum: partial (single-approver flow verified; N-of-M not verified).
                                       - `timeout` policy-set with default 30 min: verify.
                                       - **`on_timeout: DENY` never ALLOW** — verify. If a timed-out approval defaults to ALLOW anywhere, that's a P0.
                                       - `approval_expiry` 15 min binding single `gate_decision_id`: current has 5-min replay TTL (`APPROVAL_REPLAY_TTL_S`); realign.
                                       - **Deny-wins conflict rule**: verify.
                                       - Approver identity via tenant IdP (OIDC); approval signed and ledgered: partial.

                                       ---

                                       ### 1.7 Deployment Lifecycle (§14.5)

                                       **Status: ~20% built.**

                                       Existing: deploy workflows in `.github/workflows/` (`terraform.yml`, `nightly_*`, `release_bundle.yml`, backup/prune, restore-drill). Missing: the state machine INSTALL → BOOTSTRAP → ENFORCE → ROTATE → UPGRADE → ROLLBACK → EXPORT → DECOMMISSION → DESTROY with each transition itself a **ledgered event** and DESTROY producing a **destruction certificate** referencing the final anchor.

                                       ---

                                       ### 1.8 Security SLOs Per Invariant (§12.3)

                                       **Status: ~40% built.**

                                       Prometheus + Alertmanager + Grafana dashboards exist (memory: `infra/grafana-dashboards/platform-slo`, `queues`, `trust-layers`, `tenant-activity`). Explicit ATF SLO framework — each SLO measured continuously, breach = ledgered C3 event with human receipt — needs to be built on top.

                                       | ATF SLO | Current | Delta |
                                       |---|---|---|
                                       | 100% C2/C3 executed actions carry Gate decision record | Partial (Redis-backed decision record on every /execute) | Verify at 100 |
                                       | 0 silent Witness failures | Blocked on Witness | Build with Witness |
                                       | 100% anchor batches verify externally | Chain verifier exists; scheduled `aegis-verify` full-chain runs? | Verify cadence |
                                       | 100% human approvals single-action-bound | Partial | Realign per §5.7 |
                                       | 100% active agents resolve to `human_responsible` or QUARANTINED | Not present | Build with §1.4 |
                                       | Cross-implementation verifier byte-identical | Not verified (only one implementation of the verifier) | Add CI check |

                                       ---

                                       ### 1.9 Regulatory Mapping (Article 12 + DPDP)

                                       **Status: ~60% built.**

                                       Compliance module exists (`services/gateway/routers/compliance.py`, `services/audit/compliance.py`), DPDP + GRC references in code (memory: `docs/services/audit-signal-reference.md`, C1/C2 doc audit fixes 2026-06-14 late). Delta: the §8.2 Article-12-line-by-line table needs to be an **artifact-mapping** the audit-service can produce on demand — not just a document.

                                       ---

                                       ### 1.10 What's Not Built (Kernel Scope) — Recorded from ATF §13

                                       | Not to build | Why (per ATF §13) | Enforcement in this roadmap |
                                       |---|---|---|
                                       | Custom identity issuance / DID method / custom token format | Standards converging; commoditized | Removed from any phase |
                                       | Behavioral ML as CORE decision-maker | Crowded / acquired; HIGH FP burden | Present as OPTIONAL add-on (existing `services/behavior/` becomes advisory in Phase 3) |
                                       | ZK proofs | ~$0.50+/proof; no customer requirement | Appendix-only |
                                       | TEE-based execution | 15%+ overhead; not Article 12 required | Container isolation + Witness |
                                       | Federated reputation / EigenTrust | No multi-tenant network | Never |
                                       | Differential privacy sharing | No cross-org sharing | Never |
                                       | Full packet capture | Privacy hazard + storage cost | Payload hashes at API tap only |
                                       | Custom A2A protocol | Adoption friction | Never |
                                       | Global trust root consortium | Insufficient standing | Customer-chosen anchor only |

                                       ---

                                       ## 2. Phased Action Plan

                                       **Rule of the roadmap:** every phase has a measurable **exit gate** (file-checkable or metric-checkable). Nothing advances on subjective judgment.

                                       ### Phase 0 — Close Audit Debt (2 weeks)

                                       **Purpose:** the ATF kernel cannot be built on top of a mendacious CI, unapplied tamper migration, half-wired SSRF, TOCTOU on the money path, or the 17 P1s / 17 P2s enumerated in `AUDIT_2026_07_21.md`. Every ATF invariant assumes those are closed.

                                       **Sprint order** (from `AUDIT_2026_07_21.md` "Recommended sprint queue"):

                                       1. **S1** — Fix SQLAlchemy `MetaData` collisions so 4 ignored test files collect.
                                       2. **S2** — Delete `continue-on-error: true` and `--ignore` flags from `.github/workflows/test.yml`. Make CI red on real failures.
                                       3. **S3** — Split `billing_status` off `audit_logs` into `audit_billing_status` (unblocks P0-3 without breaking `services/audit/router.py:1629` UPDATE).
                                       4. **S4** — Apply migration `3a519b48a6f2` on prod-ha (audit_logs append-only trigger) — I3 becomes enforceable.
                                       5. **S5** — Silent-catches sweep: 32 `except Exception: pass` sites get `logger.exception` + `*_swallowed_total` metric. Auth-boundary sites (P0-4 sample at `gateway/routers/openai_messages.py:120-125` and P1-8) become fail-closed.
                                       6. **S6** — Guard `OPA_FAIL_MODE=open` in prod (refuse to boot if `ENVIRONMENT=="prod" and OPA_FAIL_MODE=="open"`).
                                       7. **S7** — Budget TOCTOU fix: atomic Redis Lua reserve-and-charge (P1-6).
                                       8. **S8** — X-Org-ID server-side enforcement (P1-7).
                                       9. **S9** — Unify SSRF validator (delete autonomy `_assert_safe_webhook_url`; use `sdk.common.outbound_url_allowlist.validate_outbound_url`).
                                       10. **S10** — Wire PUT-time SSRF check on `remediation.py:124` and `threatintel.py:128`.
                                       11. **S11** — Delete `X-Internal-Secret` from CORS `allow_headers`.
                                       12. **S11a–S11n** — the 14 XS/S registry / behavior / learning / insight / forensics / ARE / autonomy hygiene items. (Details in the audit sprint queue.)
                                       13. **S18b** — Enable `verify_aud=True` on Clerk JWT decode.

                                       **Exit gate:**
                                       - ✓ `pytest tests/ services/*/tests/ -m "not integration"` returns 0 failures with no `--ignore` flags.
                                       - ✓ `ruff check .` full ruleset returns 0 errors (or all remaining errors are ledgered in a real `docs/dev/lint-debt.md` with owners).
                                       - ✓ Every P0 and P1 finding in `AUDIT_2026_07_21.md` is closed or annotated with a merge-approver-accepted exception.
                                       - ✓ `alembic current` on prod-ha shows `3a519b48a6f2` applied.

                                       ---

                                       ### Phase 1a — Kernel Scaffolding (4 weeks)

                                       **Purpose:** realign existing structures to the ATF-canonical shape. No new components — just refactor + type + version.

                                       1. **Action classification C0-C3 predicate.** Migrate `services/policy/canonical.py`'s 5-tier vocabulary to the ATF C0/C1/C2/C3 predicate (§3.3). Existing 5-tier `allow/monitor/escalate/deny/quarantine` becomes the DECISION output; C0-C3 becomes the class INPUT that drives Gate/Witness/Ledger behavior. Encode as a versioned policy file so "what counted as consequential in March" is itself auditable.
                                       2. **Deterministic agent state machine.** Add `services/registry/state_machine.py` (or extend `services/registry/service.py`) that derives `VERIFIED`/`RESTRICTED`/`QUARANTINED`/`UNKNOWN` from identity validity + `contradiction_ratio_24h` (pending Witness — bootstrap with 0) + `unobserved_ratio_7d` + `escalation_ratio_30d` + `human_responsible` resolvability. Existing `agent.status` enum becomes the persisted form of the derived state. Every transition is a **C2 ledgered event**.
                                       3. **Ledger entry schema restructure.** Rewrite `services/audit/models.py::AuditLog` (or add a v3 view) into the ATF §7.1 shape:
                                          - `intent` (agent, `aegis_profile_hash`, claim, `action_class`)
                                          - `authorization` (`gate_decision_id`, decision, `policy_manifest_hash`, `constraints_evaluated[]`, `delegation_chain[]`)
                                          - `observation` (`witness_attestation_id` — nullable in Phase 1a, mandatory in 1b)
                                          - `outcome` (status, `response_hash`, `human_verification`)
                                          - `chain` (`prev_entry_hash`, `merkle_leaf`, `anchor_batch`)
                                          - Self-declared `entry_version` field. Verifier refuses unknown MAJORS.
                                       4. **RFC 8785 JCS canonicalization.** Audit `sdk/common/canonical_json.py` (or wherever `canonical_json` lives) for byte-identical RFC 8785 compliance. Add a cross-implementation CI check (Python impl × a second reference impl produce identical hashes on a corpus of 1000 fixtures). Closes SLO I6.
                                       5. **Response payload hash in `outcome`.** Every C2/C3 executed action's response (or its head + length + hash if payload is > 32 KB) is SHA-256'd and stored in `outcome.response_hash`. Gateway hooks in `gateway/proxy_helpers.py` (the post-LLM path) — the response is already available; the hash is a one-line addition.
                                       6. **Policy manifest hash in every entry.** Verify `policy_manifest_hash` is written on every decision entry; if not, add it in `services/policy/router.py` decision path.
                                       7. **Aegis Profile document (v1 shape).** Add `sdk/common/aegis_profile.py` with the §4.3 schema (subject, human_responsible, provenance, gate_policy_ref, action_class_max, tenant signature). Bootstrap-issue one per agent from the existing registry; `services/registry/router.py`'s create_agent emits the profile as its side effect.
                                       8. **P1-17 (from audit closure): destructive-action rego.** Add `services/policy/policies/destructive_actions.rego` with the KILL_AGENT / ISOLATE_AGENT / REVOKE_KEY rules; thread `input.action_type` from `_policy_gate` into the OPA input document.

                                       **Exit gate:**
                                       - ✓ Every new ledger entry carries `entry_version`, `policy_manifest_hash`, `action_class ∈ {C0,C1,C2,C3}`.
                                       - ✓ C2/C3 entries carry `response_hash`.
                                       - ✓ `aegis-verify` verifier refuses an entry with an unknown major version.
                                       - ✓ Two independent canonicalization implementations produce identical hashes on the 1000-entry test corpus.
                                       - ✓ `KILL_AGENT` invocation without `approval_id` is denied by rego and ledgered as such.
                                       - ✓ Agent state derivation returns exactly one of the 4 ATF states for every registry row (with `contradiction_ratio=0` bootstrap until Witness ships).

                                       ---

                                       ### Phase 1b — Build the Execution Witness (8 weeks)

                                       **Purpose:** the biggest missing piece and the biggest differentiator (§1). Everything ATF sells above commodity observability is the **independent attestation** that the runtime did what the Gate authorized. The Witness IS that attestation.

                                       1. **Design (Week 1).** ADR on Witness surface: sidecar container per agent pod, shared PID/network namespace, unshared trust. eBPF-CO-RE (portable) probe library selection (`bcc` vs `libbpf-rs` vs `pyroscope`-style). Signing key management (per-witness SPIFFE SVID from local SPIRE agent).
                                       2. **Sidecar skeleton (Week 2).** Container image, health endpoint, heartbeat emission to `services/audit/` every 5s. Missing heartbeat → **all subsequent verdicts flip to `UNOBSERVED`** and the audit service flags the agent state → RESTRICTED.
                                       3. **eBPF probes (Weeks 3-5).**
                                          - Network egress: socket + TLS SNI + connection metadata (5-tuple + destination hostname).
                                          - API tap: gateway-forwarded response codes + response payload SHA-256 hash.
                                          - Filesystem: `open`, `write`, `unlink` events with path (bounded, no payloads).
                                          - Process: `execve`, `fork` events.
                                          - Resource counters: per-process CPU/mem/net snapshots at 1-Hz.
                                       4. **Verdict engine (Week 6).** Given a `gate_decision_id` from the Gate and observed events within a time window, produce exactly one of:
                                          - `CORROBORATED` — observed net + api + fs (for C2/C3, all three; for C1, any).
                                          - `CONTRADICTED` — observed events conflict with claim (e.g., DELETE claim but no matching HTTP request seen, or non-2xx observed).
                                          - `UNOBSERVED` — Witness degraded OR action class wasn't instrumented (C0 without sampling).
                                       5. **Witness attestation record + signing (Week 6).** Ed25519 signed record per §6.4, canonicalized (RFC 8785), pushed to `services/audit/` for inclusion in the ledger entry's `observation` slice.
                                       6. **Gate auto-lockout on CONTRADICTED (Week 7).** Extend `services/registry/state_machine.py`: any CONTRADICTED verdict within 24h → agent state → QUARANTINED, all subsequent Gate decisions DENY until human review.
                                       7. **Reconciliation SLO (Week 7).** Continuous job: for every C2/C3 Gate decision in the last hour, verify a matching Witness attestation exists. Gap → alert (`witness_gate_reconciliation_gap_total` metric) + `UNOBSERVED` verdict written for the gap. Closes SLO I1.
                                       8. **Serverless honesty (Week 8).** Regional Gate endpoint spec + explicit `UNOBSERVED` verdicts for serverless-agent traffic (per Appendix D.1). No pretending a co-located Witness exists.
                                       9. **Threat-boundary documentation (Week 8).** §6.1 boundary lives in `docs/security/witness-trust-boundary.md` in a form auditors can read.

                                       **Exit gate:**
                                       - ✓ Every C1/C2/C3 entry in a 24-hour synthetic soak has exactly one Witness verdict (`CORROBORATED` / `CONTRADICTED` / `UNOBSERVED`) with a valid Ed25519 signature.
                                       - ✓ A staged malicious agent that claims a DELETE but no egress fires → verdict = `CONTRADICTED`, state → `QUARANTINED`, subsequent invocations → `DENY`, all four events ledgered.
                                       - ✓ Killing the Witness container mid-soak → next verdict = `UNOBSERVED`, state → `RESTRICTED`, alert fires within 30 s.
                                       - ✓ Reconciliation SLO metric shows ≥ 99.9% match between Gate decisions and Witness verdicts on the reference workload.
                                       - ✓ Witness CPU overhead < 10% per agent pod (§12.2 target).

                                       **This is the phase that matters most for winning the technical-evaluation call.**

                                       ---

                                       ### Phase 1c — Kernel Finishing (4 weeks)

                                       **Purpose:** close the last gaps between "the three components exist" and "a customer can deploy them and get a regulator-format export within 7 days" (§14.2).

                                       1. **MCP server-side proxy.** Refactor `services/mcp_server/` (currently client-side wrapper) to include a proper MCP-proxy mode: sits between agent runtime and MCP servers, intercepts `tools/call`, forwards through the Gate. This is ATF §5.1's primary interception point.
                                       2. **Egress lockdown checker.** New `scripts/ops/verify_egress_lockdown.py` — runs against a target K8s namespace / ECS task-def / VM firewall and confirms default-deny egress with the Gate as the only permitted destination. Emits a signed lockdown attestation stored in the ledger as the INSTALL event.
                                       3. **Deployment lifecycle events.** State machine in `services/gateway/routers/lifecycle.py`: `INSTALL` → `BOOTSTRAP` → `ENFORCE` → `ROTATE` → `UPGRADE` → `ROLLBACK` → `EXPORT` → `DECOMMISSION` → `DESTROY`. Every transition is a C3 ledgered event. `DESTROY` produces a **destruction certificate** referencing the final anchor — the customer can forever prove what existed and when it was destroyed.
                                       4. **Export bundle format v3.** JSON-lines entries + Merkle proofs + anchor references + policy manifests in force during the range + human-readable summary. Semver'd; verifier refuses unknown MAJORS (§7.4).
                                       5. **Article 12 mapping artifact.** Compliance service endpoint `GET /compliance/article-12-mapping` that returns the §8.2 table with live pointers to the artifacts (Gate Decision Records, Ledger entries, anchor batches, `aegis-verify` bundles, retention config, export bundles).
                                       6. **Anchor cross-signing on rotation.** Verify (or add) that when the transparency signing key rotates, the transition batch is cross-signed by both keys so chain verification survives.
                                       7. **Dry-run mode as a first-class Gate mode.** In dry-run, everything ALLOWs, everything is classified and ledgered, producing a baseline before enforcement — §5.4. Wire the mode into the deployment lifecycle so the `ENFORCE` transition is the toggle.
                                       8. **RFC 3161 timestamping authority as anchor backend.** Alternative to S3 object-lock; customer's choice (§7.2). Anchor cadence config per anchor backend.

                                       **Exit gate (this is Phase 1 exit per ATF §15):**
                                       - ✓ 1 production deployment (real customer, no toy).
                                       - ✓ ≥ 1M ledger entries written and 100% verified by `aegis-verify` including anchor checks.
                                       - ✓ Zero ledger data-loss incidents in the deployment period.
                                       - ✓ First regulator-format export bundle produced and accepted by the customer's compliance team.
                                       - ✓ Dry-run → enforcement cutover completed at that customer (INSTALL → BOOTSTRAP → ENFORCE lifecycle events all ledgered).

                                       ---

                                       ### Phase 2a — Product Layer (Months 5-8)

                                       **Purpose:** turn the kernel into a product a mid-size CISO can buy and a platform engineer can deploy without an SE on-call.

                                       1. **Policy compiler UI + testing suite (§5.4).** UI over the existing compiler + rego bundle:
                                          - Unit tests per rule (assertion cases run in CI; bundle without passing tests can't reach PUBLISHED).
                                          - Replay simulation: "under policy v18, X% of last month's ALLOWs become DENYs; here are the affected workflows."
                                          - Shadow mode: candidate policy runs in parallel with active; divergences ledgered but the active policy decides.
                                          - Coverage report: which rules fired in 30 days, which tools/action-classes have no rule other than default.
                                       2. **SCIM-integrated human-responsible flows.** Extend `services/gateway/routers/scim.py` to the SCIM agent extension shape (Okta-authored IETF draft). `human_responsible` reconciliation runs daily; orphans → QUARANTINED per invariant I5.
                                       3. **Escalation adapters (§5.7 canonical webhook + first-party adapters).** Slack, MS Teams, email, PagerDuty, ServiceNow — the existing `services/autonomy/webhook_executor.py` already covers Slack/Jira/ServiceNow; ship Teams + PagerDuty + email + a canonical webhook contract for arbitrary ITSM.
                                       4. **Entra Agent ID + Okta for AI Agents adapters.** Turnkey identity adapters (§4.2 accepted inputs #2 and #3). SPIFFE stays for K8s-native. OAuth 2.1 + DPoP as fallback.
                                       5. **RFC 3161 anchoring (finish Phase 1c #8 if deferred).**
                                       6. **Consistency sampling for C3 (§9.3).** Sample the agent's plan 3× on C3, require 2/3 constraint-consistency before the Gate forwards. Opt-in per tenant. 3× inference cost budgeted.
                                       7. **Benchmark publication (§12).** Run the §12.1 reference workload; publish the §12.2 measured table replacing every `TBD`. First sales deck cites measured, not target.

                                       **Exit gate (Phase 2 per ATF §15):**
                                       - ✓ 3 paying tenants.
                                       - ✓ 10M+ cumulative verified ledger entries.
                                       - ✓ Measured p99 Gate added latency < 20 ms on the reference workload.
                                       - ✓ Zero data-loss incidents across all tenants.
                                       - ✓ **SOC 2 Type II audit completed** (§15: an evidence vendor that hasn't passed its own audit doesn't get past procurement).
                                       - ✓ Measured cost table replaces every hypothesis in §12.2.

                                       ---

                                       ### Phase 2b — Identity Consumption Depth (parallel with 2a)

                                       **Purpose:** ADR-001 "consume identity, don't compete" made real.

                                       1. **SPIFFE SVID acceptance.** `sdk/common/spiffe_auth.py`, JWKS-like trust bundle refresh, SPIFFE-ID → tenant mapping. K8s-native customer can wire ATF into an existing SPIRE deployment in ≤ 1 hour.
                                       2. **Entra Agent ID.** `sdk/common/entra_auth.py`, JWKS from Entra tenant, Conditional Access token replay support.
                                       3. **Okta XAA / token exchange.** `sdk/common/okta_xaa.py`, RFC 8693 flow, XAA scope mapping to ATF action classes.
                                       4. **Provenance block enrichment.** Every Aegis Profile gets `model_ref`, `prompt_template_hash`, `tool_manifest_hash`, `container_image_digest`, `sbom_ref` populated from CI pipelines. Provenance changes → C2 event (§4.4).
                                       5. **Tenant issuance quota.** Contractual limit N enforced at profile-mint time; excess → C2 alert.
                                       6. **IETF AIMS composition (when the draft advances).** Reference-stack adapter combining SPIFFE + WIMSE + OAuth per draft-klrc-aiagent-auth-00.

                                       **Exit gate:** All 4 accepted identity inputs from §4.2 work end-to-end against a reference customer; no `no matching accepted-input` rejection paths remain.

                                       ---

                                       ### Phase 3 — Analytics (Months 10-18)

                                       **Purpose:** advisory analytics on top of a kernel that has been running long enough to have real ledger data. **Decisions remain deterministic per §9.2 — ML may suggest a state review, not set a state.**

                                       1. **Contradiction analytics.** Per-agent, per-tool, per-tenant contradiction rates surfaced in a dashboard. Ranked SOC triage queue.
                                       2. **Per-tenant interaction graph.** Louvain + taint from the v2.0 §10.2 approach — collusion detection across agents. Existing `services/identity_graph/` is the seed.
                                       3. **Learned trust fusion — gated (§9.2).** Ships ONLY if:
                                          - ≥ 6 months of production ledger data.
                                          - ≥ 1 confirmed incident class.
                                          - Red-team result on the corpus.
                                          - Advisory-only, never authoritative.
                                       4. **Behavioral fingerprinting add-on.** Existing `services/behavior/` becomes an opt-in add-on for customers who ask, off by default (per ADR-002 spirit).

                                       **Exit gate:** Contradiction analytics is used to triage ≥ 1 real security incident per tenant per quarter. Learned fusion (if shipped) is *never* the sole reason an agent state changes.

                                       ---

                                       ### Phase 4 — Ecosystem (Months 18+)

                                       1. Publish the ledger-entry + attestation formats as an open spec.
                                       2. Align with whatever AIMS / NCCoE demonstration projects standardize.
                                       3. Consortium anchoring only if ≥ 10 tenants demand it.

                                       ---

                                       ## 3. Dependency Graph (What Blocks What)

                                       ```
                                       Phase 0 (audit debt)
                                          │
                                          ├────────────────────┐
                                          ▼                    ▼
                                       Phase 1a               Phase 2b (identity depth)
                                       (scaffolding)          — can start after 0 finishes, in parallel with 1b/1c
                                          │
                                          ▼
                                       Phase 1b (Witness) ◀── the critical-path build
                                          │
                                          ▼
                                       Phase 1c (kernel finishing) — needs 1b to bind observation into entries
                                          │
                                          ▼
                                       Phase 1 EXIT (production deployment + 1M verified entries)
                                          │
                                          ▼
                                       Phase 2a (product layer)
                                          │
                                          ▼
                                       Phase 2 EXIT (3 tenants + SOC 2 Type II)
                                          │
                                          ▼
                                       Phase 3 (analytics — data criterion gated)
                                          │
                                          ▼
                                       Phase 4 (ecosystem)
                                       ```

                                       **Critical path length:** Phase 0 (2 wk) + 1a (4 wk) + 1b (8 wk) + 1c (4 wk) = **18 weeks to Phase 1 exit**.

                                       ---

                                       ## 4. What Not to Build in This Roadmap (Enforced)

                                       From ATF §13, mirrored so this doc is self-contained:

                                       - No custom identity issuance / DID method / token format.
                                       - No behavioral ML as the CORE decider (advisory in Phase 3 only).
                                       - No ZK proofs in the kernel.
                                       - No TEE-based execution in the kernel.
                                       - No federated reputation, no cross-org sharing, no differential privacy sharing.
                                       - No full packet capture (hashes only).
                                       - No custom A2A protocol.
                                       - No global trust root / consortium anchoring (customer-chosen only).

                                       If a proposal appears mid-quarter that lands in any of these boxes, cite this section as the reason it doesn't ship.

                                       ---

                                       ## 5. Signal for the Buyer at Every Phase

                                       - **After Phase 1c:** "Show me your AI-agent audit trail" gets a `aegis-verify`-checkable export in ≤ 7 days from install (§14.2).
                                       - **After Phase 2a:** the CISO's platform engineer deploys ATF without an SE, and the compliance team accepts the export without escalation.
                                       - **After Phase 3:** SOC triage queue prioritizes by contradiction rate; incidents surface before customer complaints.

                                       Every phase produces one artifact that survives contract review. Nothing before that milestone gets sold as production.

                                       ---

                                       ## 6. Cross-Reference

                                       - `ATF_v3.0_Verifiable_Kernel.md` — the specification this roadmap serves.
                                       - `AUDIT_2026_07_21.md` — the current-state audit (6 rounds, every finding file:line-verified). Phase 0's exact work list is that document's Sprint queue.
                                       - `INSTRUCTIONS.md` — the engineering-discipline contract every commit in every phase is built under.

                                       *End of roadmap-21.md.*
