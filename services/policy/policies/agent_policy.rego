package acp.v1.agent

import rego.v1

# =========================
# DEFAULT DECISION
# =========================

default allow := false
default reason := "no match found"

# =========================
# MAIN ENTRY POINT
# =========================

main := {
	"allow": allowed_final,
	"reason": msg_final,
	"risk_adjustment": adjustment + risk_adjustment,
	# audit S15 (P2-5): return the explainability slice the fast path
	# already produces via evaluate_full(). The slow path used to derive
	# tier + findings + policy_id from the reason-string suffix; now the
	# rego is the source of truth on both sides.
	"tier": tier_final,
	"findings": findings_final,
	"policy_id": policy_id_final,
	"explanation": msg_final,
}

# --- Explainability derivation ------------------------------------------

# tier_final honours the __escalate suffix on the reason (used by
# action_semantics_deny rules that opt into approval-required semantics
# rather than a hard block).
default tier_final := "allow"

tier_final := "escalate" if {
	not allowed_final
	endswith(msg_final, "__escalate")
}

tier_final := "deny" if {
	not allowed_final
	not endswith(msg_final, "__escalate")
}

# findings_final and policy_id_final strip the __escalate suffix so the
# SDK / SOC dashboards receive the canonical policy id.
default findings_final := []
default policy_id_final := ""

findings_final := [_policy_id_stripped] if {
	not allowed_final
}

policy_id_final := _policy_id_stripped if {
	not allowed_final
}

_policy_id_stripped := trim_suffix(msg_final, "__escalate")

# 2026-06-14 enterprise-grade: fold action-semantics deny into main.allow.
# Previously `main.allow` only depended on the permission-check `allowed`,
# so action_semantics_deny.rego was unreachable at runtime — every
# destructive command/query slipped past the slow path. This rule layers
# the semantic deny on top of the permission decision so the slow path
# matches the new fast-path Python port.
allowed_final := false if {
	action_semantics_deny
}

allowed_final := false if {
	are_destructive_deny
}

allowed_final := allowed if {
	not action_semantics_deny
	not are_destructive_deny
}

msg_final := reason if {
	action_semantics_deny
}

msg_final := are_destructive_msg if {
	are_destructive_deny
	not action_semantics_deny
}

msg_final := msg if {
	not action_semantics_deny
	not are_destructive_deny
}

# =========================
# ARE DESTRUCTIVE ACTIONS (audit P1-17)
# ARE zeroes gate_risk for KILL_AGENT / ISOLATE_AGENT / REVOKE_KEY at
# services/api/are_executor.py:229 and delegates the decision to OPA,
# but no rule was previously checking `input.metadata.action`. That
# meant the destructive-remediation authorization path was a no-op
# once ARE decided to enqueue the action. This block closes that gap.
#
# Threading: services/api/are_executor.py::_policy_gate embeds
# `metadata.action = action_type`, so the check reads
# `input.metadata.action`. Non-ARE call sites don't populate it, so
# rules below are inert for the normal /execute path.
# =========================

_are_destructive := {"KILL_AGENT", "ISOLATE_AGENT", "REVOKE_KEY"}

are_destructive_deny if {
	object.get(input, ["metadata", "action"], "") in _are_destructive
	not are_destructive_approved
}

# An operator-issued approval is threaded through as
# `input.metadata.approval_id` (see how remediation policy webhook payloads
# already carry an approval id). Presence means a human authorized the
# destructive step; absence means auto-fired by the incident watcher and
# must be denied here.
are_destructive_approved if {
	object.get(input, ["metadata", "approval_id"], "") != ""
}

are_destructive_msg := "HARD DENY: destructive remediation requires an operator approval_id" if {
	are_destructive_deny
}

# =========================
# ALLOW LOGIC
# =========================

default allowed := false

allowed if {
	# 1. Agent must be active
	lower(input.agent.status) == "active"

	# 2. Agent must not be quarantined or terminated
	not agent_suspended

	# 3. Find a matching allow permission for the requested tool
	some perm in input.agent.permissions
	perm.tool_name == input.tool
	lower(perm.action) == "allow"

	# 4. No deny override for this tool
	not has_deny_permission

	# 5. Risk score must be below critical threshold
	not input.risk_score >= 0.95

	# 6. K8s governance hard-deny must not apply
	not k8s_hard_deny
}

# Allow wildcard permission (e.g., management/system agents)
allowed if {
	lower(input.agent.status) == "active"
	not agent_suspended

	some perm in input.agent.permissions
	perm.tool_name == "*"
	lower(perm.action) == "allow"

	not has_deny_permission
	not input.risk_score >= 0.95

	# K8s hard-deny enforced within Rego (defense-in-depth; note wildcard agents bypass
	# OPA at the ToolGuard layer by design — see README.md admin bypass note)
	not k8s_hard_deny
}

# Default allow variable (queried by data/acp/v1/agent/allow)
allow := allowed

# =========================
# HELPERS
# =========================

agent_suspended if {
	lower(input.agent.status) == "quarantined"
}

agent_suspended if {
	lower(input.agent.status) == "terminated"
}

has_deny_permission if {
	some perm in input.agent.permissions
	perm.tool_name == input.tool
	lower(perm.action) == "deny"
}

# =========================
# K8S GOVERNANCE RULES
# Hard-deny rules for dangerous Kubernetes operations.
# Enforced in-policy for all OPA-evaluated agents (wildcard admins bypass OPA at ToolGuard — by design).
# Verb+resource are parsed from input.tool (format: k8s.<verb>.<resource>).
# =========================

_k8s_tool_parts := split(object.get(input, "tool", ""), ".")

# Hard deny: namespace / PersistentVolume / node deletion — catastrophic blast radius
k8s_hard_deny if {
	count(_k8s_tool_parts) >= 3
	_k8s_tool_parts[0] == "k8s"
	_k8s_tool_parts[1] in {"delete", "remove"}
	_k8s_tool_parts[2] in {
		"namespace", "namespaces",
		"persistentvolume", "persistentvolumes", "pv",
		"node", "nodes",
	}
}

# Hard deny: cluster-admin grants via clusterrolebinding (privilege escalation)
k8s_hard_deny if {
	count(_k8s_tool_parts) >= 3
	_k8s_tool_parts[0] == "k8s"
	_k8s_tool_parts[1] in {"create", "patch", "apply"}
	_k8s_tool_parts[2] in {"clusterrolebinding", "clusterrolebindings", "crb"}
	object.get(input, ["input", "extra_args", "clusterrole"], "") == "cluster-admin"
}

# =========================
# RISK ADJUSTMENT
# =========================

default adjustment := 0.0

# Spike adjustment for k8s hard-deny (destructive cluster operations)
adjustment := 0.50 if {
	k8s_hard_deny
	_k8s_tool_parts[1] in {"delete", "remove"}
}

# Higher spike for privilege escalation attempts
adjustment := 0.60 if {
	k8s_hard_deny
	_k8s_tool_parts[1] in {"create", "patch", "apply"}
}

default risk_adjustment := 0.0

# Escalate risk for high-risk agents attempting sensitive tools
risk_adjustment := 0.2 if {
	lower(input.agent.risk_level) == "high"
	input.risk_score >= 0.5
}

risk_adjustment := 0.15 if {
	lower(input.agent.risk_level) == "medium"
	input.risk_score >= 0.7
}

# Reduce risk for well-known low-risk agents (P-4 FIX: bounds-safe, no negative bypass)
risk_adjustment := -0.1 if {
	lower(input.agent.risk_level) == "low"
	input.risk_score < 0.2
	allowed
}

# =========================
# REASONING
# =========================

default msg := "no allow permission found for tool"

msg := "permission granted" if {
	allowed
}

msg := "agent is suspended" if {
	not allowed
	agent_suspended
}

msg := "agent is not active" if {
	not allowed
	not agent_suspended
	lower(input.agent.status) != "active"
}

msg := "explicit deny permission for tool" if {
	not allowed
	not agent_suspended
	has_deny_permission
}

msg := "risk score exceeds critical threshold" if {
	not allowed
	input.risk_score >= 0.95
	not k8s_hard_deny
}

msg := "HARD DENY: k8s cluster resource deletion blocked by governance policy" if {
	not allowed
	k8s_hard_deny
	_k8s_tool_parts[1] in {"delete", "remove"}
	not has_deny_permission
	not agent_suspended
	lower(input.agent.status) == "active"
}

msg := "HARD DENY: k8s privilege escalation blocked by governance policy" if {
	not allowed
	k8s_hard_deny
	_k8s_tool_parts[1] in {"create", "patch", "apply"}
	not has_deny_permission
	not agent_suspended
	lower(input.agent.status) == "active"
}
