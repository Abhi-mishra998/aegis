# Threat Intelligence

## What this page is for

Two unrelated capabilities live here:

1. **Platform threat intelligence summary** — the cross-tenant signal counts from the Intelligence module (top findings, coordinated-campaign alerts).
2. **External enrichment** — IP and domain lookups against Shodan, AbuseIPDB, and similar providers; useful during forensic triage when an audit row's metadata contains an external IOC.

## Sidebar location & role gating

- **Sidebar group**: Settings → Developer.
- **Path**: `/threat-intel`.
- **Keyboard hint**: none.
- **Minimum role**: `AUDITOR`. External enrichment can incur provider costs and is rate-limited per tenant.

## What you see

- **Summary tile band** — counts for the tenant's recent coordinated-campaign alerts, top findings, and contributions to the cross-tenant view.
- **IOC enrichment form** — input field accepting either an IP address or a domain.
- **Result panel** — shows the enrichment result (reputation score, country, ASN, last-seen timestamps, abuse confidence).

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Tenant threat summary | GET | `/threat-intel/summary` | api → intelligence |
| Enrich an IP | POST | `/threat-intel/ip` | api → external |
| Enrich a domain | POST | `/threat-intel/domain` | api → external |

## Auto-refresh & realtime

- **No auto-refresh.** The summary is read on mount; enrichment is operator-driven.

## Per-agent scoping

No. The summary is tenant-level; the enrichment lookups are caller-agnostic.

## Empty states

| Condition | Copy shown | What to do |
|---|---|---|
| Summary returns no IOCs | `No IOC lists in summary — counters only.` | Expected when the tenant has not generated any IOCs in the window. |

## Edge cases & known gotchas

- **Enrichment 429**: the per-tenant cap fired. Wait the indicated interval or contact platform admin for a higher cap.
- **Enrichment returns stale data**: results cache for 24 hours (Shodan) or 1 hour (AbuseIPDB). Force-refresh by waiting for the cache to expire.
- **Domain enrichment expects FQDN**: subdomain or full host form. The form normalizes input but extreme cases (Unicode, punycode) may need pre-normalization.
- **Summary differs from Intelligence module counts**: the summary is tenant-scoped; the Intelligence module's ZSETs are global. Disagreement is expected when the tenant has opted out of cross-tenant participation.
- **Per-EC2 flap**: `/threat-intel/*` is stable.

## Related docs

- [Learning service](../../services/learning.md) — cross-agent behavioural correlation
- [API service](../../services/api.md)
- [Forensics UI](../operations/forensics.md)

## Screenshot

![Threat Intel](../_screenshots/threat-intel.png)
