# ─────────────────────────────────────────
# STAGE 1: Builder
# ─────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DEFAULT_TIMEOUT=100

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# pyproject.toml declares packages = ["acp", "sdk", "sdk.acp_client"] and
# reads README.md for the long-description, so all three must be in the
# build context before `pip install .` can produce a wheel. The `[server]`
# extra pulls in the full FastAPI/SQLAlchemy/Redis/Groq stack the services
# need at runtime — without it the bare wheel ships only the customer SDK.
COPY pyproject.toml README.md ./
COPY acp/ ./acp/
COPY sdk/ ./sdk/

RUN pip install --no-cache-dir --default-timeout=100 --retries=3 \
    --prefix=/install ".[server]"

# ─────────────────────────────────────────
# STAGE 2: Final
# ─────────────────────────────────────────
FROM python:3.11-slim AS final

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -g 999 appuser && \
    useradd -r -u 999 -g appuser appuser && \
    chown -R appuser:appuser /app

# Copy installed packages from builder
COPY --from=builder /install /usr/local

# Bake all source into the image — volume mounts shadow this in local dev
# but ECS/EKS/standalone pulls work without any host checkout.
COPY --chown=appuser:appuser services/ ./services/
COPY --chown=appuser:appuser sdk/ ./sdk/
COPY --chown=appuser:appuser scripts/utils/seed_admin.py .

USER appuser

# Default health probe — each service overrides CMD but the health check
# assumes the service binds :8000 (uvicorn default). docker-compose services
# override this per-container.
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
