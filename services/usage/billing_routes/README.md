# services/billing — module, not a microservice

This directory is a **Python module imported by the `usage` service**. It is not a standalone microservice — there is no `main.py` and no compose entry on purpose.

Consumers:
- `services/usage/main.py` mounts `router` as the `/billing/*` router
- `services/usage/main.py` instantiates `BillingValueEngine`
- `tests/test_decision_engine.py` exercises `BillingValueEngine` directly

If you are tempted to delete this directory because it "looks dead": don't. The `usage` service will fail to import and the gateway's `POST /billing/events` chain (gateway → usage → `billing.router`) will break.

If you are restructuring the codebase, the correct move is to make this a submodule of `usage` (e.g., `services/usage/billing/`) rather than a sibling of it.
