# Aegis Documentation

* [Home](README.md)

## Introduction

* [What is Aegis?](introduction/what-is-aegis.md)
* [Why Runtime Governance](introduction/why-runtime-governance.md)
* [Quickstart](introduction/quickstart.md)
* [60-Second Tour](introduction/60-second-tour.md)
* [Demo Packs](introduction/demo-packs.md)

## Architecture

* [System Overview](architecture/system-overview.md)
* [Gateway Pipeline](architecture/10-stage-pipeline.md)
* [Flow of a Decision](architecture/flow-of-a-decision.md)
* [Data Model](architecture/data-model.md)
* [Multi-Tenancy](architecture/multi-tenancy.md)
* [Deployment Topology](architecture/deployment-topology.md)
* [UI Primitives](architecture/ui-primitives.md)

## Services

* [Services Map](services/_index.md)
* Hot path
  * [Gateway](services/gateway.md)
  * [Decision](services/decision.md)
  * [Audit](services/audit.md)
  * [Identity](services/identity.md)
  * [Policy](services/policy.md)
* Trust layer
  * [Registry](services/registry.md)
  * [Behavior](services/behavior.md)
  * [Identity Graph](services/identity-graph.md)
  * [Autonomy](services/autonomy.md)
  * [Flight Recorder](services/flight-recorder.md)
  * [Forensics](services/forensics.md)
* Operations and intelligence
  * [Billing](services/billing.md)
  * [Usage](services/usage.md)
  * [Insight](services/insight.md)
  * [Intelligence](services/intelligence.md)
  * [Learning](services/learning.md)
  * [Groq Worker](services/groq-worker.md)
  * [API](services/api.md)

## UI

* [UI Map](ui/_index.md)
* Primary nav
  * [Flight Recorder](ui/primary/flight-recorder.md)
  * [Policies](ui/primary/policies.md)
  * [Audit Trail](ui/primary/audit-trail.md)
  * [Incidents](ui/primary/incidents.md)
  * [Settings Hub](ui/primary/settings-hub.md)
* Operations
  * [Agents](ui/operations/agents.md)
  * [Identity Graph](ui/operations/identity-graph.md)
  * [Autonomy](ui/operations/autonomy.md)
  * [Forensics](ui/operations/forensics.md)
  * [Playground](ui/operations/playground.md)
  * [Live Feed](ui/operations/live-feed.md)
  * [Playbooks](ui/operations/playbooks.md)
  * [Auto Response](ui/operations/auto-response.md)
  * [Compliance](ui/operations/compliance.md)
  * [Open Source](ui/operations/open-source.md)
  * [Attack Sim](ui/operations/attack-sim.md)
  * [Kill Switch](ui/operations/kill-switch.md)
* Settings sub-pages
  * [System Health](ui/settings/system-health.md)
  * [Observability](ui/settings/observability.md)
  * [Admin Console](ui/settings/admin-console.md)
  * [Developer Panel](ui/settings/developer-panel.md)
  * [Policy Analytics](ui/settings/policy-analytics.md)
  * [Policy Sim](ui/settings/policy-sim.md)
  * [Risk Engine](ui/settings/risk-engine.md)
  * [RBAC Manager](ui/settings/rbac.md)
  * [User Management](ui/settings/user-management.md)
  * [Security Dashboard](ui/settings/security-dashboard.md)
  * [Billing](ui/settings/billing.md)
  * [Quota Management](ui/settings/quota-management.md)
  * [SSO Settings](ui/settings/sso-settings.md)
  * [Webhook Settings](ui/settings/webhook-settings.md)
  * [SIEM Settings](ui/settings/siem-settings.md)
  * [Threat Intelligence](ui/settings/threat-intel.md)
  * [Scheduled Reports](ui/settings/scheduled-reports.md)

## Security

* [Cryptographic Audit Chain](security/crypto-audit-chain.md)
* [JWT Authentication](security/jwt-auth.md)
* [RBAC Roles](security/rbac-roles.md)
* [Kill Switch](security/kill-switch.md)
* [OPA Policies](security/opa-policies.md)
* [Threat Scenarios](security/threat-scenarios.md)
* [Secret Management](security/secret-management.md)

## Operations

* [Deployment](operations/deployment.md)
* [Backup and Restore](operations/backup-restore.md)
* [Key Rotation](operations/key-rotation.md)
* [Soak Tests](operations/soak-tests.md)
* [Tenant Data Requests](operations/tenant-data-requests.md)
* [Observability](operations/observability.md)
* Runbooks
  * [Audit Chain Violation](operations/runbooks/audit-chain-violation.md)
  * [Kill Switch Engaged](operations/runbooks/kill-switch-engaged.md)
  * [Rate Limit Spike](operations/runbooks/rate-limit-spike.md)

## API

* [Reference](api/reference.md)
* [Authentication](api/authentication.md)
* [Error Codes](api/error-codes.md)
* [Examples](api/examples.md)
