# Reference Audit Report — AEVF Evidence Verification

> **Status:** Reference template. Audit firms may fork this document and adapt it for engagement use. Material in **\[brackets\]** is meant to be replaced engagement-by-engagement. Material in `code blocks` is verbatim and should not be altered.
>
> **Spec version:** `aevf/0.1.0` ([spec.md](./spec.md))
> **License:** Apache 2.0
> **Companion document:** [`auditor-checklist.md`](./auditor-checklist.md) (the procedural workpaper this report concludes)

---

## **\[Audit firm letterhead\]**

# Independent verification report on AI-decision evidence package

**To:** \[The Board of Directors / Audit Committee of \[Auditee Co.\]\]
**Subject:** Verification of the AI-decision evidence package covering the period \[YYYY-MM-DD\] through \[YYYY-MM-DD\] under the AEVF v0.1.0 specification.
**Engagement reference:** \[Firm engagement ID\]
**Date of report:** \[YYYY-MM-DD\]

---

## 1. Subject of the engagement

\[Auditee Co.\] (the "Company") has implemented an automated AI-decision governance pipeline (the "System") that records each tool-call decision made by the Company's AI agents as a cryptographically signed record. The Company has produced an evidence package (the "Bundle") covering the period above, conforming to the open Aegis Evidence Verification Format specification version `aevf/0.1.0` (the "Specification").

The Company is responsible for:

- the design and operation of the System;
- the policy that produced the recorded decisions;
- the preparation and integrity at rest of the Bundle.

Our responsibility is to express a conclusion as to whether the Bundle, as presented, verifies under the V1–V6 checks defined in §15 of the Specification, and whether the sampling and reconciliation procedures described in §2 below identified any material discrepancies.

## 2. Methodology

We followed the procedure documented in the AEVF Auditor Checklist v0.1.0 (the "Checklist"), specifically steps 0 through 7. We performed the following:

| Step | What we did | Output |
|---|---|---|
| 0.1 — 0.4 | Confirmed verifier independence (firm-built or PyPI `aegis-verify`), reproducibility from the Specification, and that verification ran on a network-isolated host. | Independence workpaper W-001. |
| 1.1 — 1.3 | Recorded chain-of-custody SHA-256 of the Bundle as received; parsed it; recorded its declared `format_version`, `tenant_id`, `framework`, and `period`. | Bundle metadata workpaper W-002. |
| 2.1 — 2.3 | Ran `aegis-verify` (or our firm's independent verifier) over the Bundle and recorded the V1–V6 outcomes. | Verifier output W-003 (attached). |
| 3.1 — 3.4 | Recomputed and matched the public-key fingerprints; confirmed every `kid` referenced by a Merkle root is present in `bundle.public_keys`; cross-referenced to prior-engagement archived fingerprints. | Key-custody workpaper W-004. |
| 4.1 — 4.5 | Sampled \[N\] records using AICPA AT-C 105 sampling guidance; tested the recorded decisions for vocabulary conformance, named-reason traceability, escalation-follow-up, and mapping completeness. | Sampling workpaper W-005. |
| 5.1 — 5.3 | Tested retention metadata against EU AI Act Art. 26(6) (≥6 months) and India DPDP Rules Nov 2025 (≥12 months) where applicable. | Retention workpaper W-006. |
| 6.1 — 6.2 | Reconciled \[N\] sampled records to the Company's SIEM (\[Splunk / Datadog / Sentinel / Chronicle\]) to test completeness of the Bundle against the Company's other monitoring. | Cross-source workpaper W-007. |

---

## 3. Findings

### 3.1 V1–V6 verification outcome

The Bundle **\[passed / failed\]** all six checks defined in §15 of the Specification. The verifier output, retained at W-003, reports:

```
[paste verifier output here — example:]

aegis-verify report
  bundle:     aegis-evidence-bundle/2026-06
  framework:  eu-ai-act
  tenant:     [tenant_id]
  records:    [N]
  keys:       [K]
  roots:      [R]

  [PASS] V1_bundle_format_recognized
  [PASS] V2_event_hash_recompute        — [N] rows pass
  [PASS] V3_prev_hash_chain_per_shard   — [S] shards, 0 breaks
  [PASS] V4_merkle_root_signatures      — [R] roots verified
  [PASS] V5_prev_root_hash_chain        — chain intact
  [PASS] V6_retention_metadata_consistent

*** PASS *** every signature, hash chain, and Merkle root verifies.
```

### 3.2 Sampling findings

| Test | Result |
|---|---|
| Decision vocabulary conformance (Step 4.2) | \[N of N\] sampled decisions are within the canonical set. |
| Named-reason traceability for denials (Step 4.3) | \[N of D\] denied decisions cite a known reason string. |
| Escalation follow-up (Step 4.4) | \[N of E\] escalated decisions have a recorded human override or open approval. |
| Mapping completeness (Step 4.5) | \[N of N\] sampled records carry at least one `mappings.[framework]` entry. |

### 3.3 Retention finding

| Item | Value | Required | Conclusion |
|---|---|---|---|
| `configured_retention_days` | \[180 / 365 / …\] | \[≥180 for EU AI Act Art. 26(6); ≥365 for DPDP\] | \[Meets / Gap of N days\] |
| Earliest row in bundle | \[YYYY-MM-DD\] | within configured window | \[Honest / Inconsistent\] |

### 3.4 Cross-source reconciliation finding

We tested \[N\] AEVF records against the Company's SIEM. \[N matched / X unmatched\]. \[Discrepancies were resolved by … / Discrepancies remain open as Finding F-_____\].

### 3.5 Findings raised

\[List each finding with: F-number, severity, control reference, management response, target remediation date. If none: "We identified no findings during this engagement."\]

---

## 4. Limitations

This verification is subject to the following limitations:

- **Scope of the cryptographic conclusion.** The V1–V6 checks prove that the records in the Bundle have not been altered since they were signed and that the daily Merkle roots form an unbroken chain across the engagement period. They do **not** prove that all decisions made by the System during the period were captured in the Bundle — a record that was never signed cannot be missing from a bundle that was never written to include it. Completeness is tested by the cross-source reconciliation in Step 6 to the standard of the Company's other monitoring tooling.
- **Policy correctness is out of scope.** A decision recorded as `allow` may, in principle, reflect a policy that should have denied. This engagement evaluates the *integrity of the record*, not the *correctness of the policy* that produced it.
- **Legal admissibility.** A passing verification is *evidence mapped to* the EU AI Act, NIST AI RMF, SOC 2, and Indian DPDP requirements named in [`auditor-checklist.md` Appendix A](./auditor-checklist.md#appendix-a-control-id-cross-reference). Whether this evidence is *legally admissible* in any specific jurisdiction is a question for legal counsel and not addressed here.
- **Reliance on the Specification.** Our conclusion assumes the AEVF v0.1.0 specification is correct. The Specification has been published openly and is open to peer review; we have not, in this engagement, independently audited the Specification itself.

---

## 5. Conclusion

\[**Choose one:**\]

> **Unmodified opinion.** Based on the procedures described in §2 and the findings recorded in §3, we have verified that the AEVF evidence package presented covers the period \[period.start\] through \[period.end\] for tenant \[tenant_id\] and that, to the standard of the Specification, the records it contains have not been altered since they were signed.

> **Modified opinion.** Based on the procedures described in §2, the Bundle verifies under V1–V6 of the Specification, but we identified the findings noted in §3.5 above. The Company has \[X\] days to respond.

> **Disclaimer.** We are unable to express a conclusion. Reason: \[…\]

This report does not constitute an audit of the Company's financial statements or of the design and operating effectiveness of the System as a whole. It is limited to the evidence verification work described above.

---

## 6. Sign-off

```
─────────────────────────────────────────────────────────────────────
Signed: ____________________________________________
        \[Audit partner name\] · \[Title\] · \[Firm\]
        \[YYYY-MM-DD\]

Reviewed: ____________________________________________
          \[Reviewing partner name\] · \[Title\]
          \[YYYY-MM-DD\]

Independence: We declare we are independent of the Company in
accordance with \[ICAI / AICPA / IESBA Code applicable to the
engagement\].

Engagement quality control review: \[performed / waived per policy\]
─────────────────────────────────────────────────────────────────────
```

---

## Appendix A — Workpaper index

| ID | Title | Source step |
|---|---|---|
| W-001 | Verifier independence + environment | Checklist 0.1–0.4 |
| W-002 | Bundle chain-of-custody + metadata | Checklist 1.1–1.3 |
| W-003 | Verifier output (V1–V6) | Checklist 2.1–2.3 |
| W-004 | Key custody — fingerprint recompute + cross-reference | Checklist 3.1–3.4 |
| W-005 | Sample selection + decision testing | Checklist 4.1–4.5 |
| W-006 | Retention testing | Checklist 5.1–5.3 |
| W-007 | Cross-source SIEM reconciliation | Checklist 6.1–6.2 |

## Appendix B — Form of management's representation letter

We recommend the engagement obtain a representation letter from Company management acknowledging:

1. The Bundle as transmitted to us is the complete bundle for the period.
2. No records were removed from the bundle prior to transmittal.
3. The Company's retention policy is `configured_retention_days = [N]` and the Company will preserve all rows referenced in the bundle for the duration of the retention window.
4. The public keys disclosed in `bundle.public_keys` are the complete set of signing keys used during the period; no other keys signed any record now in production.
5. The mappings in `bundle.records[].mappings` reflect the Company's good-faith assessment of the controls each record evidences.

A sample letter is available on request from the firm's audit-quality team.

## Appendix C — Why this template is open

This template is published under Apache 2.0 because the AEVF audit category does not exist if no audit firm has a deliverable they can use. We expect firms will adapt, sharpen, and improve this document; the openness of the spec is what makes that economic. Forks are welcome, attribution requested.

For changes to the spec itself, see [`spec.md` Appendix B — Spec change log](./spec.md#appendix-b-spec-change-log).
