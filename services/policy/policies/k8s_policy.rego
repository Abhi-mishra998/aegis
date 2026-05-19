package acp.v1.k8s

import rego.v1

# =============================================================================
# ACP Kubernetes Governance Policy
# =============================================================================
# Evaluated for tools matching "k8s.*" prefix.
# Returns:
#   allow             bool   — true if the operation is permitted
#   reason            string — human-readable explanation
#   risk_adjustment   float  — delta applied to composite risk score
#   requires_approval bool   — true when operator gate is required
# =============================================================================

default allow := false
default reason := "no matching allow rule"
default risk_adjustment := 0.0
default requires_approval := false

# ── Main entry point ──────────────────────────────────────────────────────────

main := {
    "allow":             allow,
    "reason":            reason,
    "risk_adjustment":   risk_adjustment,
    "requires_approval": requires_approval,
}

# ── Read-only operations: always allowed ──────────────────────────────────────

allow if {
    _is_readonly_verb
    not _is_high_risk_secret_read
}

reason := "read-only operation permitted" if {
    allow
    _is_readonly_verb
}

_is_readonly_verb if {
    verb := _verb
    verb in {"get", "list", "describe", "logs", "top", "watch"}
}

# Treat bulk secret list as medium risk even if technically read-only
_is_high_risk_secret_read if {
    _verb == "list"
    _resource in {"secret", "secrets"}
}

allow if {
    _verb in {"get", "describe"}
    _resource in {"secret", "secrets"}
}

# ── Safe mutations: scale, apply configmap ────────────────────────────────────

allow if {
    _verb == "scale"
    not _is_production_resource
}

reason := "scaling permitted (non-production)" if {
    allow
    _verb == "scale"
    not _is_production_resource
}

# Production scaling requires approval
requires_approval if {
    _verb == "scale"
    _is_production_resource
}

reason := "production scaling — approval required" if {
    not allow
    _verb == "scale"
    _is_production_resource
}

allow if {
    _verb == "apply"
    _resource in {"configmap", "configmaps"}
    not _is_production_resource
}

reason := "configmap apply permitted (non-production)" if {
    allow
    _verb == "apply"
    _resource in {"configmap", "configmaps"}
}

# ── HARD DENY: namespace deletion ─────────────────────────────────────────────

reason := "HARD DENY: namespace deletion is never permitted by automation" if {
    not allow
    _verb in {"delete", "remove"}
    _resource in {"namespace", "namespaces"}
}

risk_adjustment := 0.5 if {
    _verb in {"delete", "remove"}
    _resource in {"namespace", "namespaces"}
}

# ── HARD DENY: persistent-volume deletion ────────────────────────────────────

reason := "HARD DENY: PersistentVolume deletion risks data loss" if {
    not allow
    _verb in {"delete", "remove"}
    _resource in {"persistentvolume", "persistentvolumes", "pv"}
}

risk_adjustment := 0.45 if {
    _verb in {"delete", "remove"}
    _resource in {"persistentvolume", "persistentvolumes", "pv"}
}

# ── HARD DENY: node deletion ──────────────────────────────────────────────────

reason := "HARD DENY: node deletion destabilizes the control plane" if {
    not allow
    _verb in {"delete", "remove"}
    _resource in {"node", "nodes"}
}

risk_adjustment := 0.50 if {
    _verb in {"delete", "remove"}
    _resource in {"node", "nodes"}
}

# ── HARD DENY: cluster-admin grants ──────────────────────────────────────────

reason := "HARD DENY: cluster-admin privilege escalation blocked" if {
    not allow
    _is_admin_grant
}

risk_adjustment := 0.60 if {
    _is_admin_grant
}

_is_admin_grant if {
    _verb in {"create", "patch", "apply"}
    _resource in {"clusterrolebinding", "clusterrolebindings"}
    role := object.get(input, ["input", "extra_args", "clusterrole"], "")
    role == "cluster-admin"
}

# Also block any RBAC create/patch that isn't explicitly allow-listed
reason := "RBAC mutation requires explicit operator approval" if {
    not allow
    _verb in {"create", "patch", "apply"}
    _resource in {"clusterrole", "clusterroles", "clusterrolebinding", "clusterrolebindings"}
    not _is_safe_rbac_op
}

requires_approval if {
    _verb in {"create", "patch", "apply"}
    _resource in {"clusterrole", "clusterroles", "clusterrolebinding", "clusterrolebindings"}
}

_is_safe_rbac_op if {
    role := object.get(input, ["input", "extra_args", "clusterrole"], "")
    role in {"view", "payments-reader", "monitoring-reader"}
}

# ── Production mutation gate ──────────────────────────────────────────────────

requires_approval if {
    _is_production_resource
    _verb in {"delete", "patch", "apply", "scale"}
}

reason := "production namespace mutation — approval required" if {
    not allow
    _is_production_resource
    _verb in {"delete", "patch", "apply"}
}

# ── exec access ───────────────────────────────────────────────────────────────

reason := "exec access requires explicit approval" if {
    not allow
    _verb == "exec"
}

requires_approval if {
    _verb == "exec"
}

# ── Risk adjustments for dangerous patterns ───────────────────────────────────

risk_adjustment := 0.35 if {
    allow
    _verb in {"delete", "remove"}
    _resource in {"pod", "pods"}
}

risk_adjustment := 0.15 if {
    allow
    _verb == "scale"
    _is_production_resource
}

risk_adjustment := 0.20 if {
    allow
    _resource in {"secret", "secrets"}
    _verb in {"get", "describe"}
}

risk_adjustment := 0.30 if {
    allow
    _resource in {"secret", "secrets"}
    _verb == "list"
}

# ── Helpers ───────────────────────────────────────────────────────────────────

_verb := lower(input.input.verb) if {
    input.input.verb != null
} else := split(input.tool, ".")[1]

_resource := lower(input.input.resource) if {
    input.input.resource != null
} else := split(input.tool, ".")[2] if {
    count(split(input.tool, ".")) >= 3
} else := ""

_namespace := lower(object.get(input, ["input", "namespace"], "default"))

_is_production_resource if {
    _namespace == "production"
}
