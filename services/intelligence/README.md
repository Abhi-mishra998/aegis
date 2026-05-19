# services/intelligence — module, not a microservice

This directory is a **Python module imported by the `behavior` service**. It is not a standalone microservice — there is no compose entry and no dedicated port.

The `main.py` file is a development stub exposing a single HTTP endpoint for local testing. It is **not deployed** in production.

Consumers:
- `services/behavior/service.py` imports `intelligence_engine` directly for in-process anomaly scoring

If you need to expose intelligence scoring as a standalone API, promote this to a full service with its own `Dockerfile` entry and compose definition.
