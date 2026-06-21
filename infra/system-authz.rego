package system.authz

import rego.v1

# P0-2 fix (2026-06-21): OPA admin port `:8181` was accepting unauthenticated
# `PUT /v1/policies/*` from anyone on the EC2 host, which let an attacker with
# RCE in any service upload `default allow := true` to the aegis package and
# bypass every governance decision across the fleet.
#
# This authz rule runs for EVERY HTTP request OPA receives (enabled by the
# `--authorization=basic` flag in docker-compose.yml). default=false makes
# anything not explicitly allowed below return 403.
#
# OPA 1.17.1 requires `import rego.v1` to use the `if` keyword; without the
# import, `if` is rejected as a parse error. With the import, the `allow {`
# v0 syntax is also rejected. So this file uses v1 throughout.
#
# The bundle-pull (OPA polling bundle-server for `acp/bundle.tar.gz`) does
# NOT go through this authz layer — it's internal to OPA's bundle-plugin.

default allow := false

# 1. Docker healthcheck.
allow if {
    input.path == ["health"]
}

# 2. Prometheus scrape.
allow if {
    input.path == ["metrics"]
}

# 3. The hot path: policy/decision-svc calls `POST /v1/data/<pkg>/<rule>`
#    with the input body for evaluation.
allow if {
    input.method == "POST"
    input.path[0] == "v1"
    input.path[1] == "data"
}

# 4. Occasional `GET /v1/data/<pkg>` for status/introspection by ops scripts.
allow if {
    input.method == "GET"
    input.path[0] == "v1"
    input.path[1] == "data"
}

# 5. NOT allowed (default deny catches):
#    - PUT  /v1/policies/*     ← the P0-2 attack vector
#    - DELETE /v1/policies/*
#    - PATCH /v1/data/*
#    - any other admin endpoint
