# Execution Witness — Trust Boundary

**Applies to:** `services/witness/*` — the ATF v3.2 §6 Execution Witness.

**Purpose:** State exactly what the Witness defends against, what it does
NOT defend against, and how those limits are visible to an operator.

## What the Witness assumes trusted

1. **The host kernel.** eBPF probes, syscall tracepoints, and network taps
   run at kernel level. A malicious kernel can lie to userspace about what
   happened.
2. **The container runtime** (Docker daemon, containerd, CRI-O). A
   compromised runtime can inject sidecars, redirect network namespaces,
   or falsify pod-level identity.
3. **The signing-key storage medium** — either the SSM parameter that
   supplies `WITNESS_SIGNING_KEY_PEM` or the volume mount at
   `WITNESS_SIGNING_KEY_PATH`. A compromise here lets an attacker mint
   valid attestations.
4. **The Redis instance used by `services/witness/store.py`**. A
   compromise here lets an attacker inject or delete observations.

Compromise of any of these places is **out of Witness scope**.

## What the Witness defends against

A **compromised or dishonest agent runtime process** — an LLM harness,
CrewAI worker, or any other agent code that:

- Reports success on an action that never happened (execution fraud).
- Claims one action but performs a different one (claim-vs-action mismatch).
- Falsifies its own observability output (post-hoc log editing).

The Witness's independence from the agent process — separate container,
separate signing key, separate storage — is the trust boundary.

## Bounded properties (limits, not eliminations)

Two mechanisms bound the impact of an out-of-scope compromise, without
removing it:

**(1) Heartbeat-loss visibility.** If the Witness is killed or its probes
tampered with such that it stops emitting heartbeats, all subsequent
verdicts flip to `UNOBSERVED` after `_HEARTBEAT_STALE_SECONDS` (default
30s). The gap is *visible* to the auditor — silence is not usable to fake
CORROBORATED evidence.

**(2) Anchor-bounded taint.** Already-anchored ledger batches cannot be
retroactively rewritten (append-only chain + external anchor per §7.2).
Host compromise taints evidence only *forward from the compromise*, and
only until the next anchor cycle (`anchor_batch` field per §7.1). A
customer who archives external anchor references sees the taint window.

## What we do NOT claim

- We do not claim protection against a malicious host operator.
- We do not claim protection against a compromised kernel or container
  runtime.
- We do not claim to detect an attacker with `WITNESS_SIGNING_KEY_PEM` in
  hand.
- We do not claim serverless deployments produce evidence — they
  explicitly emit `UNOBSERVED` per Appendix D.1 (see
  `services/witness/router.py::_DEPLOYMENT_MODE`).

Customers whose threat model includes malicious hosts should combine ATF
with confidential computing (SEV-SNP / TDX) or external attestation. That
integration is not in the current kernel; the `attestation_version` field
in the attestation record is versioned per §7.4 to accommodate it later
without breaking existing verifiers.

## Operator checklist

Before treating a Witness deployment as evidentiary:

- [ ] `WITNESS_SIGNING_KEY_PEM` or `WITNESS_SIGNING_KEY_PATH` is set;
      dev-mode ephemeral key is refused in production
      (`services/witness/signer.py::_resolve_key`).
- [ ] `WITNESS_DEPLOYMENT_MODE=sidecar` (or explicit `serverless` with
      the understood UNOBSERVED consequence).
- [ ] Redis is reachable and `services/witness/store.py::get_backend_kind`
      returns `"redis"` — the memory fallback is single-process and does
      NOT share evidence across workers.
- [ ] Egress lockdown checker passes for the deployment target
      (`scripts/ops/verify_egress_lockdown.py`).
- [ ] External anchor writes are current — check the transparency
      scheduler emitted its last root inside the expected cadence.

Failure of any checklist item narrows the trust boundary of the
resulting evidence. State that narrowing in the export.
