# SIEM Settings

## What this page is for

The configuration page for SIEM forwarding. Operators wire Splunk HEC or Datadog Logs endpoints here so audit events are pushed into the company's existing log management.

## Sidebar location & role gating

- **Sidebar group**: Settings → Developer.
- **Path**: `/siem`.
- **Keyboard hint**: none.
- **Minimum role**: `ADMIN`.

## What you see

- **Provider tabs** — Splunk and Datadog.
- **Splunk section** — HEC URL field, HEC token field, source-type override.
- **Datadog section** — API key field, site selector (`datadoghq.com`, `datadoghq.eu`, etc.), source override.
- **Test button** — sends a sample event to verify connectivity.
- **Manual push button** — fires `POST /siem/push` with a configurable limit to backfill recent audit rows on demand.
- **Save button** — persists the config.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Read current config | GET | `/siem/config` | api |
| Save config | POST | `/siem/config` | api |
| Test Splunk | POST | `/siem/test/splunk` | api |
| Test Datadog | POST | `/siem/test/datadog` | api |
| Push recent events to SIEM | POST | `/siem/push` | api → audit |

## Auto-refresh & realtime

- **No auto-refresh.**

## Per-agent scoping

No. SIEM forwarding is tenant-level.

## Empty states

The form renders even when no SIEM is configured.

## Edge cases & known gotchas

- **Test returns 401**: HEC token or Datadog API key invalid. The platform never logs the secret value; correct in the form and retry.
- **Forwarding eventual**: a SIEM push failure does not block the originating audit row. The chain stays intact; the SIEM forwarder retries.
- **Splunk `source` override**: useful when the same Aegis tenant feeds multiple Splunk indexes. Default source is `aegis_audit`.
- **Manual push limit**: capped at 10,000 rows per call to prevent inadvertent flooding.
- **Secrets in transit**: HEC tokens travel as bearer tokens in the Authorization header to Splunk; ensure the HEC endpoint is HTTPS.
- **Per-EC2 flap**: `/siem/*` proxies via the strict-prefix `siem/` rule.

## Related docs

- [API service](../../services/api.md)
- [Audit service](../../services/audit.md)

## Screenshot

![SIEM Settings](../_screenshots/siem-settings.png)
