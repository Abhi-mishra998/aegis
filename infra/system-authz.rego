package system.authz

# P0-2 fix (2026-06-21): OPA admin port `:8181` was accepting unauthenticated
# `PUT /v1/policies/*` from anyone on the EC2 host, which let an attacker with
# RCE in any service upload `default allow := true` to the aegis package and
# bypass every governance decision across the fleet.
#
# This authz rule runs for EVERY HTTP request OPA receives (enabled by the
# `--authorization=basic` flag in docker-compose.yml). default=false makes
# anything not explicitly allowed below return 403.
#
# The bundle-pull (OPA polling bundle-server for `acp/bundle.tar.gz`) does
# NOT go through this authz layer — it's internal to OPA's bundle-plugin.

default allow := false

# 1. Docker healthcheck + Prometheus scrape.
allow if {
    input.path == ["health"]
}

allow if {
    input.path == ["metrics"]
}

# 2. The hot path: policy/decision-svc calls `POST /v1/data/<pkg>/<rule>`
#    with the input body for evaluation.
allow if {
    input.method == "POST"
    input.path[0] == "v1"
    input.path[1] == "data"
}

# 3. Occasional `GET /v1/data/<pkg>` for status/introspection by ops scripts.
allow if {
    input.method == "GET"
    input.path[0] == "v1"
    input.path[1] == "data"
}

# 4. NOT allowed (default deny catches):
#    - PUT  /v1/policies/*     ← the P0-2 attack vector
#    - DELETE /v1/policies/*
#    - PATCH /v1/data/*
#    - any other admin endpoint
