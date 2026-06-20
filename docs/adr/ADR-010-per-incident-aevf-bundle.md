# ADR-010: Per-incident AEVF bundle as a first-class download

* Status: Accepted
* Date: 2026-06-21
* Deciders: Abhishek Mishra (founder/CTO)
* Tags: audit, forensics, incidents, compliance, ux

## Context

We already had two AEVF bundle paths before Sprint EI-19:

- `/compliance/verifiable-bundle/{framework}?period_start=…&period_end=…`
  — period-scoped, covers every audit row in the window.
- `/compliance/export/{bundle_type}` — same shape, different framework
  pre-sets.

Both serve "full SOC 2 evidence window" use cases. Neither answers the
question every CISO actually asks during an incident review:

> "Show me the cryptographically verifiable audit record for THIS
>  specific incident — just the events tied to it, nothing else."

The period-scoped paths force the user to compute a window that
includes the incident (and probably much more), then trust them to
mentally filter. That's:

- High friction (operator has to guess the period).
- Bigger file to attach to a Jira/SNOW ticket (more rows = more bytes).
- Less precise — the auditor sees 1000 unrelated rows next to the
  100 they care about.

## Decision

We will ship a **per-incident AEVF bundle endpoint** as a first-class
download, separate from the period-scoped endpoints:

- Backend: `POST /compliance/incident-bundle` on audit-svc accepts
  `{audit_ids: [...], framework, incident_number}` — filters
  `audit_logs` by the explicit id list (typically `incident.related_
  audit_ids`), then runs the same `generate_verifiable_bundle()`
  generator with the auto-narrowed period.
- Gateway: `GET /incidents/{id}/aevf-bundle` orchestrates — fetches
  the incident from api-svc, calls audit-svc with the right
  `audit_ids`, streams the JSON back.
- UI: a "AEVF bundle" button next to "Export PDF" on the Incident
  detail panel. One click, no period picker.
- The generator (`services/audit/verifiable_bundle.py:generate_
  verifiable_bundle`) gains an optional `audit_ids` keyword arg —
  backward-compatible with the existing 5-arg callers.

## Alternatives considered

1. **Just reuse `/compliance/verifiable-bundle/{framework}`** with a
   carefully-derived period. Rejected because:
    - Operator has to compute period_start = incident.created_at
      and period_end = incident.resolved_at, both ISO-8601, both
      URL-encoded. UX dies on contact.
    - Period boundaries are inclusive on both sides — operator has
      to remember +1ms tricks to avoid off-by-one. Already a
      support-ticket trap.
    - Bundle contains every other tenant event in that window, so
      the file is meaningfully larger to attach to a Jira/SNOW
      ticket (10× typical for an incident that spans business hours).
2. **Materialise the bundle eagerly when the incident closes** (e.g.
   a background job that builds the bundle and stores the URL on the
   incident row). Rejected:
    - Operator might never download it — pre-computing is wasted work.
    - Cached bundles go stale if the chain is rotated or a key is
      replaced; on-demand generation always reflects current state.
3. **Embed the bundle inline in the incident's PDF export.** Rejected
   — the PDF is for humans (CISO triage), the AEVF bundle is for
   machines (`aegis-verify`). Mixing them confuses both audiences.
4. **Add the audit_ids filter directly to the existing
   /verifiable-bundle/{framework} endpoint** as a query parameter.
   Cleaner code-wise but: (a) the existing endpoint's contract is
   period-based and adding an id-list as a query string breaks the
   URL-length budget at ~50 ids, (b) the per-incident endpoint
   benefits from default-period elision (operator never specifies
   one), which would require special-casing the existing endpoint.

## Consequences

* **Positive**
  - One-click download — operator never thinks about periods.
  - Bundle is the minimum necessary — only the rows tied to this
    incident, smallest possible attachment for a Jira/SNOW ticket.
  - Cryptographic verification still works exactly the same — the
    auditor runs `aegis-verify --bundle <file>` and gets V1–V6 PASS.
  - The `related_audit_ids` JSON column on `incidents` was already
    populated by the existing pipeline (Sprint 16 incident-watcher);
    we get to leverage it without a new ingestion path.
  - Per-incident-bundle is the more-natural unit for the ITSM
    round-trip (EI-17/EI-18) — attach to the ticket on close, audit
    chain travels with the ticket.
* **Negative**
  - Two endpoints now exist for what's conceptually the same thing.
    Mitigated by docstring + ADR.
  - If an incident's `related_audit_ids` was never populated (legacy
    incidents from before EI-19), the bundle is empty. The generator
    handles empty correctly — bundle is valid, with zero rows + a
    valid chain proof of "no events in this period" — but the operator
    sees an empty bundle. Document this as a known limitation.
* **Reversibility**
  - **Trivial.** The new endpoint is additive; existing
    `/verifiable-bundle/{framework}` is unchanged. Deletion is just
    removing the new route + the optional kwarg + the UI button.

## Implementation references

* `services/audit/verifiable_bundle.py:193-235` — `generate_verifiable_
  bundle` with new `audit_ids` keyword
* `services/audit/compliance.py:1087-1140` — `POST /compliance/
  incident-bundle` handler + `_IncidentBundleRequest` schema
* `services/gateway/routers/incidents.py` (tail) — `GET /incidents/
  {id}/aevf-bundle` orchestrator
* `services/gateway/_rbac_map.py:127` — SECURITY_ANALYST+ rule
* `ui/src/pages/Incidents.jsx` — `_exportIncidentAevf` + button
* `tests/test_ei19_incident_aevf_bundle.py` — 17 cases
* `docs/security/jira-itsm-setup.md` §7 + `docs/security/servicenow-
  itsm-setup.md` "Attaching the AEVF bundle" — operator runbooks

## Verification

```bash
# 1. Pick a recent incident from the API.
INC_ID=$(curl -sS -H "Authorization: Bearer $JWT" https://aegisagent.in/incidents \
  | jq -r '.data[0].id')

# 2. Download its AEVF bundle.
curl -sS -OJ -H "Authorization: Bearer $JWT" \
  "https://aegisagent.in/incidents/$INC_ID/aevf-bundle?framework=eu-ai-act"
# expect: a file named aegis-incident-INC-NNN-eu-ai-act-…json lands in cwd

# 3. Verify it offline — no Aegis service needed.
pip install aegis-aevf
aegis-verify --bundle aegis-incident-INC-NNN-eu-ai-act-*.json
# expect: V1-V6 PASS, n rows verified.
```
