# Aegis Documentation

* [Home](README.md)

## AEVF — Open Verification Standard

* [Overview](AEVF/README.md)
* [Specification (aevf/0.1.0)](AEVF/spec.md)
* [Auditor Checklist](AEVF/auditor-checklist.md)
* [Reference Audit Report Template](AEVF/reference-audit-report.md)
* [Reference Evidence Package](AEVF/reference-bundle.md)

## Introduction

* [What is Aegis?](introduction/what-is-aegis.md)
* [Why Runtime Governance](introduction/why-runtime-governance.md)
* [60-Second Tour](introduction/60-second-tour.md)
* [Quickstart](introduction/quickstart.md)
* [Demo Packs](introduction/demo-packs.md)

## Architecture

* [System Overview](architecture/system-overview.md)
* [Gateway Pipeline](architecture/10-stage-pipeline.md)
* [Decision Explorer](architecture/decision-explorer.md)
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
    * [Audit Signal Reference](services/audit-signal-reference.md)
  * [Identity](services/identity.md)
  * [Policy](services/policy.md)
* Trust layer
  * [Registry](services/registry.md)
  * [Behavior](services/behavior.md)
  * [Identity Graph](services/identity-graph.md)
  * [Autonomy](services/autonomy.md)
  * [Flight Recorder](services/flight-recorder.md)
  * [Forensics](services/forensics.md)
* Aggregation
  * [Insight](services/insight.md)
  * [Learning](services/learning.md)
  * [Usage](services/usage.md)
  * [API](services/api.md)

## UI

* [UI Map](ui/_index.md)
* Primary nav
  * [Live Demo](ui/primary/live-demo.md)
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
  * [Attack Sim](ui/operations/attack-sim.md)
  * [Kill Switch](ui/operations/kill-switch.md)
  * [Open Source](ui/operations/open-source.md)
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
  * [Quota Management](ui/settings/quota-management.md)
  * [Billing](ui/settings/billing.md)
  * [SSO Settings](ui/settings/sso-settings.md)
  * [Webhook Settings](ui/settings/webhook-settings.md)
  * [SIEM Settings](ui/settings/siem-settings.md)
  * [Threat Intelligence](ui/settings/threat-intel.md)
  * [Scheduled Reports](ui/settings/scheduled-reports.md)

## Voice Guide

* [Overview](voice-guide/_index.md)
* [UI Integration](voice-guide/ui-integration.md)
* [RAG and LLM Strategy](voice-guide/rag-and-llm.md)
* [Deployment and Operations](voice-guide/deployment.md)

## Integrations

* [SDK Wrappers (PyPI)](integrations/sdk-wrappers.md)
* [SDK 1.1.0 Release](integrations/sdk-1.1.0-release.md)
* [Evidence Export Adapters (SIEM + GRC)](integrations/evidence-export.md)

## Security

* [Cryptographic Audit Chain](security/crypto-audit-chain.md)
* [Detection Pipeline](security/detection-pipeline.md)
* [JWT Authentication](security/jwt-auth.md)
* [Mesh Authentication](security/mesh-auth.md)
* [RBAC Roles](security/rbac-roles.md)
* [Kill Switch](security/kill-switch.md)
* [OPA Policies](security/opa-policies.md)
* [Threat Scenarios](security/threat-scenarios.md)
* [Threat Model](THREAT_MODEL.md)
* [Secret Management](security/secret-management.md)
* [SOC 2 Tracker](security/soc2_tracker.md)
* [Vendor Security One-Pager](security/vendor-security-onepager.md)

## Operations

* [Deployment](operations/deployment.md)
* [Backup and Restore](operations/backup-restore.md)
* [Key Rotation](operations/key-rotation.md)
* [SIEM Forwarders](operations/siem-forwarders.md)
* [Soak Tests](operations/soak-tests.md)
* [Tenant Data Requests](operations/tenant-data-requests.md)
* [Observability](operations/observability.md)
* Runbooks
  * [Audit Chain Violation](runbooks/audit_chain_violation.md)
  * [Key Rotation](runbooks/key_rotation.md)
  * [Key Rotation Drill Log](runbooks/key_rotation_drill_log.md)
  * [Restore Drill](runbooks/restore_drill.md)
  * [Tenant Data Request](runbooks/tenant_data_request.md)
  * [Kill Switch Engaged](operations/runbooks/kill-switch-engaged.md)
  * [Rate Limit Spike](operations/runbooks/rate-limit-spike.md)

## API

* [Reference](api/reference.md)
* [Authentication](api/authentication.md)
* [Error Codes](api/error-codes.md)
* [Examples](api/examples.md)
