# Settings Hub

## What this page is for

The Settings page is a navigation surface, not a feature in its own right. It groups the 18 Settings sub-pages into four logical sections — Access Control, Operations, Developer, and Account — and lets an operator jump to the right one without scrolling the sidebar. The primary nav links here; the sub-pages are reached through this index.

## Sidebar location & role gating

- **Sidebar group**: Primary nav (last item).
- **Path**: `/settings`.
- **Keyboard hint**: `G S`.
- **Minimum role for the hub itself**: any authenticated user can open `/settings`. Each sub-page enforces its own role gate — opening the Settings hub is free; the linked pages are not.

## What you see

A header followed by four sections, each with a list of cards. Every card has an icon, a label, a short description, and a click target that routes to the sub-page.

### Access Control (4 cards)

| Label | Route | Purpose |
|---|---|---|
| RBAC Manager | `/rbac` | Roles, permissions, tenant scopes |
| User Management | `/users` | Invite, manage roles, deactivate |
| Security Ops | `/security` | Authentication + secrets posture |
| SSO Configuration | `/sso` | SAML 2.0 / OIDC single sign-on |

### Operations (5 cards)

| Label | Route | Purpose |
|---|---|---|
| System Health | `/system-health` | Service status + queue depth |
| Observability | `/observability` | Metrics, traces, SLO dashboards |
| Admin Console | `/admin` | Platform health + tenant activity |
| Policy Analytics | `/policy-analytics` | Hit rates, FP rates, coverage gaps |
| Quota Management | `/quota` | Request limits + inference cost caps |

### Developer (7 cards)

| Label | Route | Purpose |
|---|---|---|
| Developer Panel | `/developer` | API keys, SDK examples |
| Policy Simulation | `/policy-sim` | Dry-run policies on historical events |
| Playbooks | `/playbooks` | Automated incident response sequences |
| Webhook Settings | `/webhook-settings` | Slack, PagerDuty, generic hooks |
| SIEM Integration | `/siem` | Splunk HEC + Datadog Logs push |
| Threat Intelligence | `/threat-intel` | IP + domain enrichment feeds |
| Scheduled Reports | `/scheduled-reports` | Automated PDF delivery to stakeholders |

### Account (2 cards)

| Label | Route | Purpose |
|---|---|---|
| Usage & Billing | `/billing` | Consumption, invoices, plan |
| Risk Engine (preview) | `/risk` | Behavioral scoring — experimental |

## Backend calls

*None.* The Settings page is a pure navigation hub. It makes no API calls of its own; every sub-page makes its own calls when opened. The card list is hard-coded in `ui/src/pages/Settings.jsx`.

## Auto-refresh & realtime

*Not applicable.* No data to refresh.

## Per-agent scoping

*Not applicable.* The hub does not scope anything; sub-pages that scope do so individually via the sidebar `useAgents` picker.

## Empty states

*Not applicable.* The card list is static. Cards never disappear based on data.

## Edge cases & known gotchas

- **A sub-page route is missing or renamed**: the card still renders and clicking it 404s in the client router. Keep the `to:` values in `Settings.jsx` aligned with the routes registered in `ui/src/App.jsx`.
- **A user with no role**: the hub itself loads even when the user is `VIEWER`; the linked sub-pages enforce their own roles. Cards are not hidden based on role — a `VIEWER` who clicks "User Management" sees the 403 inside that page.
- **Search / filter**: the hub has no search box today. With 18 cards it has not been needed; if the count grows beyond 25 a filter input becomes worthwhile.
- **Per-EC2 SPA routing**: every sub-page route is a React Router path. Browser navigation works because nginx serves `index.html` on `Accept: text/html` per the canonical configuration; JS fetches inside the sub-page hit the gateway as JSON. See [Deployment Topology](../../architecture/deployment-topology.md) for the nginx contract.

## Related docs

- [System Health UI](../settings/system-health.md)
- [Observability UI](../settings/observability.md)
- [Admin Console UI](../settings/admin-console.md)
- [Developer Panel UI](../settings/developer-panel.md)
- [RBAC UI](../settings/rbac.md)
- [User Management UI](../settings/user-management.md)
- [Billing UI](../settings/billing.md)
- [Quota Management UI](../settings/quota-management.md)
- [SSO Settings UI](../settings/sso-settings.md)
- [Webhook Settings UI](../settings/webhook-settings.md)
- [SIEM Settings UI](../settings/siem-settings.md)
- [Threat Intel UI](../settings/threat-intel.md)
- [Scheduled Reports UI](../settings/scheduled-reports.md)
- [Policy Analytics UI](../settings/policy-analytics.md)
- [Policy Sim UI](../settings/policy-sim.md)
- [Risk Engine UI](../settings/risk-engine.md)
- [Security Dashboard UI](../settings/security-dashboard.md)

## Screenshot

![Settings hub](../_screenshots/settings-hub.png)
