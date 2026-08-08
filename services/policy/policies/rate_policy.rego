package acp.v1.rate

import rego.v1

# =========================
# RATE LIMITING POLICY
# Enterprise rate/budget enforcement rules
# =========================

# SEC-2026-07-31 (H8): default is DENY. The former `default allow := true`
# was the only rego file in the repo with a permissive default — a tool
# name that didn't match the destructive-substring set (send_email,
# transfer_funds, run_query_readonly, ...) got `allow=true` even at
# critical risk. Now every path must be explicitly allowed.
default allow := false
default reason := "no explicit allow rule matched"

# Allow when the two hard-deny rules below don't trigger AND risk isn't
# at the absolute ceiling. Baseline: agents whose risk_score is below
# 1.0 and who are either non-critical OR calling a non-sensitive tool.
allow if {
	input.risk_score < 1.0
	not _critical_and_sensitive
	not _above_ceiling
}

_critical_and_sensitive if {
	lower(input.agent.risk_level) == "critical"
	sensitive_tool
}

_above_ceiling if {
	input.risk_score >= 1.0
}

# =========================
# SENSITIVE TOOL DEFINITIONS
# =========================

sensitive_tool if {
	destructive_tools := {"delete", "drop", "truncate", "exec", "shell", "sudo", "rm"}
	some t in destructive_tools
	contains(lower(input.tool), t)
}

# =========================
# REASONING
# =========================

reason := "within rate limits" if {
	allow
}

reason := "critical risk agent blocked from sensitive tool" if {
	_critical_and_sensitive
}

reason := "maximum risk threshold exceeded" if {
	_above_ceiling
}
