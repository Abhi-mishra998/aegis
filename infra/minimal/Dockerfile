# Minimal-mode self-host image.
#
# R3 — Sprint refactor: collapse the 18-container prod-ha mesh into ONE
# container (`aegis-core`) so a self-host customer's security team can
# review the deployment in an afternoon. All Python services run as
# distinct uvicorn processes on localhost ports inside this container,
# supervised by supervisord. OPA runs as a sidecar process. Database
# and Redis stay external — those are the 2 other containers.
#
# Build:
#   docker build -t aegis/core:minimal -f infra/minimal/Dockerfile .
#
# Run (with the 3-service compose):
#   docker compose -f infra/minimal/docker-compose.minimal.yml up -d

ARG PY_BASE=python:3.11-slim
FROM ${PY_BASE} AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    AEGIS_MODE=minimal

# System deps: supervisord (process manager), curl (healthchecks),
# ca-certs (TLS to Groq + outbound), and postgresql-client (psql is used
# by start.sh for the boot-time database wait + bootstrap; alembic and
# the runtime services use asyncpg, but the entrypoint relies on the
# plain client.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        supervisor \
        curl \
        ca-certificates \
        postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# OPA binary — we run it as a sidecar process inside this container.
# Pinned version so a security review sees a stable artifact. Matches
# the OPA running on prod-ha (`openpolicyagent/opa:latest-debug` →
# v1.17.1 at time of writing).
ARG OPA_VERSION=1.17.1
RUN curl -sSL -o /usr/local/bin/opa \
        "https://openpolicyagent.org/downloads/v${OPA_VERSION}/opa_linux_$(dpkg --print-architecture)_static" \
    && chmod +x /usr/local/bin/opa

# Python deps — single requirements file ensures the whole stack
# resolves cleanly. The repo's `pyproject.toml` declares
# `packages = ["acp", "sdk", "sdk.acp_client"]` and `readme = "README.md"`,
# so the wheel build needs those paths inside `/tmp/aegis/` to succeed.
COPY pyproject.toml /tmp/aegis/
COPY README.md      /tmp/aegis/
COPY acp            /tmp/aegis/acp
COPY sdk            /tmp/aegis/sdk
RUN pip install --no-cache-dir /tmp/aegis/[server]

# Application code.
WORKDIR /app
COPY . /app

# Supervisord configuration + entrypoint.
COPY infra/minimal/supervisord.conf /etc/supervisor/conf.d/aegis-core.conf
COPY infra/minimal/start.sh         /usr/local/bin/aegis-start
RUN chmod +x /usr/local/bin/aegis-start

# Healthcheck — single endpoint that aggregates the 7 inner services.
HEALTHCHECK --interval=20s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://localhost:8000/system/health >/dev/null || exit 1

# Single externally-exposed port. All inner services bind 127.0.0.1.
EXPOSE 8000

ENTRYPOINT ["/usr/local/bin/aegis-start"]
