"""ATF v3.2 §6 — Execution Witness.

Sidecar service that observes what the runtime ACTUALLY did (network,
API, filesystem, process, resource) and emits one of three verdicts
against every C1-C3 Gate decision:

    CORROBORATED — observations consistent with the claim
    CONTRADICTED — observations conflict with the claim
    UNOBSERVED   — Witness degraded or class not instrumented

The Witness assumes the host kernel + container runtime are trusted
(§6.1). It defends against a compromised or dishonest AGENT PROCESS,
not a compromised host.

This directory contains the LOGIC + INTERFACES + signed attestation
records; the eBPF probes are ops-side infrastructure (per-platform).
Probes emit Observation events into the /observations endpoint; the
verdict engine consumes them; the ledger `observation` slice is filled
via the attestation id.
"""
