# sdk.intelligence — shared cross-agent anomaly intelligence

This module was moved from `services/intelligence/` to `sdk/intelligence/` in
sprint-4 (S3-8 carry-over). It is a **library**, not a microservice:

- No FastAPI app.
- No Dockerfile, no compose entry.
- No HTTP port allocation.
- Pure Python + Redis state.

## Consumers

- `services/behavior/service.py` imports `intelligence_engine` directly for
  in-process anomaly scoring.

## Why under `sdk/`?

The previous `services/intelligence/` location implied this was a deployable
service. `infra/docker-compose.yml` never had a container for it, which
confused new readers and led the audit-30 / audit-v2 / principal-engineer
reviews to flag it as a "fake microservice."

`sdk/` is the right home: it's where shared library code lives that is
imported into multiple services' processes. `sdk.common` already follows
this pattern.

## If you need to promote it to a standalone service

1. Create a new `services/intelligence/` directory with a `main.py` exposing
   the desired HTTP surface.
2. Add a container block to `infra/docker-compose.yml` with its own port +
   healthcheck.
3. Update consumers to call the HTTP endpoint instead of importing the
   module directly.
4. Move the Redis state to a per-tenant namespace if cross-tenant data
   leakage is now a concern.
