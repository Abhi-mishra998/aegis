# AEVF Auditor Checklist

> **Audience:** External auditors, internal-audit teams, GRC analysts who need to verify an AEVF evidence package without trusting the vendor that produced it.
> **Spec version:** `aevf/0.1.0` ([spec.md](./spec.md))
> **License:** Apache 2.0 — fork this document, mark it up for your engagement, sign it.

This checklist walks through every step an audit practitioner performs to verify an AEVF evidence package. Every step states:

- **What you do** — the literal action
- **What you observe** — the output you record as evidence
- **What it proves** — the cryptographic or procedural claim
- **Which regulation/control it satisfies** — the mapping back to EU AI Act, SOC 2, NIST AI RMF, and India DPDP

The checklist is structured so that an auditor can complete it in **~25 minutes** for a single bundle and produce a signed sign-off page (last section) ready for the engagement file.

---

## 0. Before you start — independence + environment

| # | Action | Evidence to retain |
|---|---|---|
| 0.1 | Confirm you have downloaded the AEVF specification at the version the bundle declares: `aegis-verify --print-spec-version` matches `bundle.format_version`. | Screenshot of CLI output + the spec PDF/markdown for the file. |
| 0.2 | Confirm the verifier binary's source is the published reference implementation (or your firm's independently-built verifier). If using `aegis-verify` from PyPI: record `pip show aegis-verify` (version, location, dependencies). | `pip show aegis-verify` output. |
| 0.3 | Verify the verifier is reproducible-from-spec. If your firm has built its own AEVF verifier, run **both** verifiers over the bundle in steps 2–7 and confirm identical PASS/FAIL conclusions. | Diff of the two verifier reports (should be byte-identical for PASS/FAIL conclusions). |
| 0.4 | **Disable networking on the machine you run the verifier on.** AEVF's value proposition is "no network call to the vendor." Verify this empirically — disconnect Wi-Fi / ethernet, or use a firewall rule that blocks egress, OR run inside a container with `--network=none`. | A screenshot showing networking is disabled before step 2 begins. |

> **Independence note.** Steps 0.3 and 0.4 are what distinguishes an AEVF audit from a vendor-dashboard audit. The verifier is the witness; if it can't witness without the vendor, the audit is just trust by another name.

---

## 1. Bundle receipt + integrity at rest

| # | Action | Evidence | Proves | Regulation |
|---|---|---|---|---|
| 1.1 | Receive the bundle from the auditee. Record the SHA-256 of the file as received: `sha256sum bundle.json`. | The hex digest, time of receipt, sender's signed email/transmittal note. | Chain-of-custody from the auditee to your evidence file. | EU AI Act Art. 12(1) — record-keeping; SOC 2 CC7.2 |
| 1.2 | Parse the bundle as JSON. Reject if not well-formed. | Parser error if any. | The bundle is at minimum syntactically valid. | EU AI Act Art. 12(1) |
| 1.3 | Read `bundle.format_version`, `bundle.tenant_id`, `bundle.framework`, `bundle.period.start`, `bundle.period.end`, `bundle.generated_at`. Confirm period.end > period.start, generated_at ≥ period.end. | Copy of the metadata block. | The bundle declares what it covers and when it was produced. | EU AI Act Art. 12(1)(a)(b); NIST AI RMF GOVERN-1.4 |

---

## 2. Run the canonical verifier (V1–V6)

| # | Action | Expected output | Proves | Regulation |
|---|---|---|---|---|
| 2.1 | `aegis-verify --bundle bundle.json --verbose --json > report.json` | Six PASS lines (V1 through V6) and exit code 0. | The bundle conforms to AEVF; rows are not altered; the chain is intact; signatures verify; retention is honest. | EU AI Act Art. 12 (record integrity), Art. 14 (oversight evidence); SOC 2 CC7.1, CC7.2; NIST AI RMF MEASURE-2.7; DPDP Sec. 8(5) |
| 2.2 | If exit code ≠ 0, record `report.json.first_broken_row_id` and the failing check name. **Stop** — escalate as a finding before proceeding. | Failing check name, broken row id. | The bundle is not auditor-grade in its current form; the auditee must produce a corrected bundle or explain the gap. | — |
| 2.3 | Diff your verifier's exit conclusion against the auditee's claim (auditee's submission letter usually states "we believe this bundle verifies"). | A confirmation that auditor and auditee agree, OR a recorded disagreement to investigate. | The auditee's claim about their own evidence is testable. | EU AI Act Art. 12(2) — providers' record-keeping responsibility |

---

## 3. Public-key chain of custody

| # | Action | Evidence | Proves | Regulation |
|---|---|---|---|---|
| 3.1 | For each entry in `bundle.public_keys`, compute the fingerprint: `sha256(pem).hexdigest()[:32]`. Confirm it equals `entry.kid`. | The recomputed fingerprint matching `kid` for each key. | The bundle's key IDs are not mis-labeled. | EU AI Act Art. 12; SOC 2 CC6.1 (logical access) |
| 3.2 | Confirm each `kid` referenced by a `merkle_roots[i].kid` is present in `bundle.public_keys`. | Set membership check. | No "ghost" signing key; every signer is disclosed in the bundle. | SOC 2 CC6.1 |
| 3.3 | If your firm has archived a fingerprint for this auditee from a prior engagement: confirm at least one current key matches a previously-archived fingerprint (or there is a documented key rotation event). | Cross-reference to prior engagement file. | Identity continuity of the signer between engagements. | EU AI Act Art. 12; ISO 27001 A.5.17 |
| 3.4 | Confirm each key's `valid_from`/`valid_to` window covers the period of the bundle. | Date arithmetic. | The signer was authorized to sign during the period the rows cover. | SOC 2 CC6.1; ISO 27001 A.8.3 |

---

## 4. Decision-record sampling (substantive testing)

The verifier proves *integrity*, not *correctness*. Step 4 samples records and confirms the recorded decision matches what the producer's policy would have produced.

| # | Action | Evidence | Proves | Regulation |
|---|---|---|---|---|
| 4.1 | Select a sample of N records from `bundle.records` using your firm's sampling standard (typical: 25 records, mix of allow/deny/escalate, mix of agents, spanning the period uniformly). | Sample list with row ids. | Sampling methodology documented for review. | SOC 2 (sampling per AICPA AT-C 105); EU AI Act Art. 14(4) — human oversight evidence |
| 4.2 | For each sampled record, confirm the recorded `decision` is one of `{allow, deny, escalate, kill, monitor, error}`. Anything outside that set is a finding. | Decision-value frequency table. | Vocabulary of decisions matches the published canonical set. | NIST AI RMF MEASURE-2.7 |
| 4.3 | For each sampled `decision == "deny"` or `"kill"`: confirm `metadata_json.reason` (if present) cites a known policy rule (one of the published reason strings — `destructive_sql_ddl`, `system_path_access`, `external_pii_exfil`, `bulk_pii_egress_above_threshold`, `k8s_prod_namespace_destruction`, `destructive_shell_command`, …). Unknown reasons are findings. | Reason-string distribution. | Denials are traceable to a named, published policy rule. | EU AI Act Art. 14 (oversight); Art. 13 (transparency) |
| 4.4 | For each sampled `decision == "escalate"`: confirm there is a corresponding `human_override_events` row (in a separate bundle slice or appendix) referencing the same `request_id`, OR an open approval that has not yet been resolved. | Cross-reference table: request_id → override_event_id or "pending." | Escalations are followed up by a human operator. | EU AI Act Art. 14(4) — natural persons assigned to oversight |
| 4.5 | For each sampled record, confirm `mappings` references at least one control ID from the framework declared at `bundle.framework`. (e.g. an `eu-ai-act` bundle should have `mappings.eu_ai_act` populated.) | Mapping completeness rate. | The bundle's framework declaration is honored at the record level. | EU AI Act Art. 12(2) |

---

## 5. Retention + period coverage

| # | Action | Evidence | Proves | Regulation |
|---|---|---|---|---|
| 5.1 | Confirm `retention_metadata.configured_retention_days` meets the applicable minimum: ≥180 days for EU AI Act Art. 26(6); ≥365 days for India DPDP records. | Comparison table. | The producer's retention policy is policy-compliant. | EU AI Act Art. 26(6); DPDP §8(5) + Rules Nov 2025 |
| 5.2 | Confirm `retention_metadata.earliest_row_in_bundle` is not older than `configured_retention_days`. | Date arithmetic (V6 already checks this; you are confirming the auditor-side conclusion). | The producer's retention claim is honest. | EU AI Act Art. 26(6) |
| 5.3 | Confirm the bundle's `period.start` aligns with the auditee's stated record-keeping period. If the engagement covers Q2 2026, the bundle should cover at least 2026-04-01 → 2026-06-30. | Period vs. engagement scope. | The bundle is scoped to the engagement. | EU AI Act Art. 12(1)(c) |

---

## 6. Cross-source reconciliation (if SIEM is in scope)

The verifier proves the bundle is internally consistent. Step 6 proves the bundle is consistent with the auditee's other monitoring systems.

| # | Action | Evidence | Proves | Regulation |
|---|---|---|---|---|
| 6.1 | If the auditee uses Splunk/Datadog/Sentinel/Chronicle: request a SIEM extract for the same period. For each AEVF record carrying `mappings.[any]`, confirm a matching SIEM record exists keyed on `request_id` (or equivalent correlation field). | Counts: `aevf_records` vs. `siem_records_matched` vs. `siem_records_unmatched`. | The bundle is not a curated subset hiding adverse decisions. | EU AI Act Art. 12(2); SOC 2 CC7.2 |
| 6.2 | If reconciliation finds AEVF records with no SIEM counterpart (or vice versa), document the gap and request reconciliation from the auditee. | Reconciliation memo. | Completeness, to the standard of the auditee's other tooling. | SOC 2 CC7.2; EU AI Act Art. 12 |

> **Note.** AEVF v0.1.0 does not require SIEM reconciliation, but a Big-4 engagement is unlikely to omit it. Sprint A6 of Aegis will ship an AEVF→SIEM evidence adapter so each SIEM row carries a back-reference to the verifiable AEVF bundle.

---

## 7. Sign-off (the page that goes into the engagement file)

```
─────────────────────────────────────────────────────────────────────
AEVF Evidence Verification — Sign-off

Engagement:                ____________________________________________
Auditee:                   ____________________________________________
Bundle filename:           ____________________________________________
Bundle SHA-256:            ____________________________________________
Bundle format_version:     aegis-evidence-bundle/2026-06
Spec version:              aevf/0.1.0
Verifier implementation:   _____________________  version ____________

Verification result:        [  ] PASS — all six checks (V1–V6) passed
                            [  ] FAIL — failing check: ___________  row: __________

Sample size (Step 4.1):    N = ______
Sample decision mix:       allow: ____  deny: ____  escalate: ____  other: ____

Retention finding (5.1):   configured = _____ days  required ≥ _____ days  [ ] meets [ ] gap

Cross-source reconciled?:  [ ] Yes — SIEM matched _____ / _____ records
                           [ ] N/A — auditee uses no other monitoring tool
                           [ ] No — gap recorded as finding F-______

Findings raised:           ____________________________________________
                            ____________________________________________

Conclusion (one of):
  [ ] We have verified that the AEVF evidence package presented covers
      the period _______________ for tenant _______________________ and
      that, to the standard of the AEVF v0.1.0 specification, the records
      it contains have not been altered since they were signed.
  [ ] We have verified the bundle but identified the findings noted; the
      auditee has _____ days to respond.
  [ ] We are unable to opine. Reason: ___________________________________

Signed:                    ____________________________________________
                            Auditor                             Date

                            ____________________________________________
                            Reviewer                            Date
─────────────────────────────────────────────────────────────────────
```

---

## Appendix A. Control-ID cross-reference

The table below maps the most common audit checks to the regulatory requirements they evidence. Auditors building a custom checklist for an engagement may extract this section and re-paste it next to their own workpapers.

| AEVF check / step | EU AI Act | SOC 2 (CC) | NIST AI RMF | India DPDP |
|---|---|---|---|---|
| V1 bundle format recognized | Art. 12(1) | CC7.2 | GOVERN-1.4 | §8(5) |
| V2 event_hash recompute | Art. 12(1); Art. 12(2) | CC7.2 | MEASURE-2.7 | §8(5) |
| V3 prev_hash chain intact | Art. 12(1); Art. 12(2) | CC7.2 | MEASURE-2.7 | §8(5) |
| V4 Merkle root signatures | Art. 12(1); Art. 13 | CC6.1, CC7.2 | MEASURE-2.7 | §8(5) |
| V5 cross-day root chain | Art. 12(1); Art. 26(6) | CC7.2 | MEASURE-2.7 | §8(5) |
| V6 retention honest | Art. 26(6) | CC7.2, CC8.1 | GOVERN-1.4 | §8(5); Rules Nov 2025 |
| Step 3 (key custody) | Art. 12 | CC6.1 | GOVERN-2.1 | §8(5) |
| Step 4 (decision sampling) | Art. 13; Art. 14 | CC4.1 | MAP-3.1 | — |
| Step 5 (retention) | Art. 26(6) | CC8.1 | — | Rules Nov 2025 |
| Step 6 (cross-source) | Art. 12(2) | CC7.2 | MANAGE-4.2 | — |

(Mappings are guidance based on a plain reading of the published frameworks; they are not legal advice and a control framework owner at your firm should ratify them for engagement use.)

## Appendix B. Tooling

- **Reference verifier:** `pip install aegis-aevf` ([PyPI](https://pypi.org/project/aegis-aevf/) — Sprint A7)
- **Reference implementation source:** [`tools/aegis_verify/` on GitHub](https://github.com/Abhi-mishra998/aegis/tree/main/tools/aegis_verify)
- **Spec:** [`spec.md`](./spec.md)
- **Reference Audit Report template:** [`reference-audit-report.md`](./reference-audit-report.md) (this checklist's natural companion document)
