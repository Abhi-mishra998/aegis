# Webhook Settings

## What this page is for

The outbound-notification configuration page. Operators wire Slack, PagerDuty, and generic HTTP webhooks here so the platform can notify external systems when incidents open or auto-response rules fire.

## Sidebar location & role gating

- **Sidebar group**: Settings → Developer.
- **Path**: `/webhook-settings`.
- **Keyboard hint**: none.
- **Minimum role**: `ADMIN`. Webhook configuration is sensitive (URLs can be SSRF targets if misconfigured).

## What you see

- **Slack section** — incoming-webhook URL field plus a Test button.
- **PagerDuty section** — routing key field plus a Test button.
- **Generic webhook section** — URL field, optional auth-header field, Test button.
- **Save button** — persists all sections.
- **Test result panel** — shows the HTTP status returned by the external service after a test.

## Backend calls

| Action | HTTP | API path | Service |
|---|---|---|---|
| Read current config | GET | `/webhooks/config` | api |
| Save config | POST | `/webhooks/config` | api |
| Test Slack | POST | `/webhooks/test/slack` | api |
| Test PagerDuty | POST | `/webhooks/test/pagerduty` | api |
| Test generic webhook | POST | `/webhooks/test/webhook` | api |

## Auto-refresh & realtime

- **No auto-refresh.**

## Per-agent scoping

No. Webhooks are tenant-level.

## Empty states

The page renders even when no webhooks are configured.

## Edge cases & known gotchas

- **Test returns SSRF reject**: the platform rejects URLs pointing at localhost or private IP ranges at save time. The test endpoint also enforces this. Use a public host or expose the internal service through a public proxy.
- **Slack test succeeds but real notifications never arrive**: the Slack workspace may have rate-limited the integration. Inspect Slack's "App Activity" view.
- **Generic webhook auth header**: the platform supports a single static header; for more complex auth (e.g., HMAC signing) implement a small proxy in front of the receiver.
- **Webhook DLQ growing**: failed deliveries land in `acp:webhook_dlq:{tenant_id}`. Operators can inspect via the API and replay.
- **Per-EC2 flap**: `/webhooks/*` proxies via the strict-prefix `webhooks/` rule.

## Related docs

- [API service](../../services/api.md)
- [Incidents UI](../primary/incidents.md)
- [Auto Response UI](../operations/auto-response.md)

## Screenshot

![Webhook Settings](../_screenshots/webhook-settings.png)
