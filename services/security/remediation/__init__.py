"""Sprint 6 — Auto-Remediation Framework.

When a Sprint 4 incident transitions to `quarantined`, the executor here
fires the per-tenant remediation policy: revoke the API key, kill any
in-flight JWTs for the agent, page on-call via webhook, write an audit
row. Each action is recorded into a ledger so the SOC can audit "what
did Aegis do for me" without grepping gateway logs.

Submodules:
  policy.py    — RemediationPolicy dataclass + per-tenant load
  actions.py   — RemediationAction dataclass + KIND_* constants
  executor.py  — orchestrator: per-incident, fan-out, ledger write
  webhooks.py  — HTTP POST with bounded retry for on-call paging
"""
from . import actions, executor, policy, webhooks  # noqa: F401

__all__ = ["actions", "executor", "policy", "webhooks"]
