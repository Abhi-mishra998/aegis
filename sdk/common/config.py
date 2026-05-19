"""
ACP Shared Configuration
========================
Single source of truth for all service settings.
Supports both Docker and Local environments cleanly.
"""

from __future__ import annotations

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class ACPSettings(BaseSettings):
    """
    Centralized configuration for all ACP services.

    - Works for Docker (service DNS)
    - Works for Local (override via .env)
    - No hardcoded localhost mistakes
    """

    model_config = SettingsConfigDict(
        # Search for .env in current dir, then root dir
        env_file=(".env", "../../.env", "../.env"),
        extra="ignore",
        case_sensitive=True,
        env_file_encoding="utf-8",
    )

    # ─────────────────────────────────────────────────────────────
    # 🔥 Infrastructure (REQUIRED — NO DEFAULTS IN PROD)
    # ─────────────────────────────────────────────────────────────

    DATABASE_URL: str = Field(
        ...,
        description="PostgreSQL connection string"
    )

    REDIS_URL: str = Field(
        ...,
        description="Redis connection string"
    )

    ENVIRONMENT: str = Field(default="development")
    LOG_LEVEL: str = Field(default="INFO")

    # ─────────────────────────────────────────────────────────────
    # 🔐 Security / JWT
    # ─────────────────────────────────────────────────────────────

    JWT_SECRET_KEY: str = Field(
        ...,
        description="JWT signing secret (MUST be set)"
    )

    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_EXPIRY_MINUTES: int = Field(default=15)

    # ─────────────────────────────────────────────────────────────
    # 🌐 External Services
    # ─────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = Field(
        ...,
        description="Groq API key for the insight service (REQUIRED)"
    )
    GROQ_MODEL: str = Field(
        default="llama-3.3-70b-versatile",
        description="LLM model for background threat analysis (high quality)"
    )
    GROQ_MODEL_FAST: str = Field(
        default="llama-3.1-8b-instant",
        description="LLM model for hot-path inline decisions (lowest latency)"
    )
    INTERNAL_SECRET: str = Field(
        ...,
        description="Shared secret for service-to-service mesh authentication (REQUIRED)"
    )

    OPA_URL: str = Field(
        default="http://acp_opa:8181"
    )
    OPA_FAIL_MODE: str = Field(
        default="closed",
        description="'closed' = deny on OPA failure (default, safe); 'open' = allow on OPA failure (use only for dev/staging)"
    )

    # ─────────────────────────────────────────────
    # 🔗 Internal Service URLs (Defaults for local development)
    # ─────────────────────────────────────────────
    REGISTRY_SERVICE_URL: str = Field(default="http://localhost:8001")
    IDENTITY_SERVICE_URL: str = Field(default="http://localhost:8002")
    POLICY_SERVICE_URL: str = Field(default="http://localhost:8003")
    AUDIT_SERVICE_URL: str = Field(default="http://localhost:8004")
    API_SERVICE_URL: str = Field(default="http://localhost:8005")
    BEHAVIOR_SERVICE_URL: str = Field(default="http://localhost:8007")
    DECISION_SERVICE_URL: str = Field(default="http://localhost:8010")
    USAGE_SERVICE_URL: str = Field(default="http://localhost:8006")
    INSIGHT_SERVICE_URL: str = Field(default="http://localhost:8011")
    FORENSICS_SERVICE_URL: str = Field(default="http://localhost:8012")
    # 2026-05-13: next-gen Runtime Trust Infrastructure
    IDENTITY_GRAPH_SERVICE_URL: str = Field(default="http://localhost:8013")
    FLIGHT_RECORDER_SERVICE_URL: str = Field(default="http://localhost:8014")
    AUTONOMY_SERVICE_URL: str = Field(default="http://localhost:8015")

    # Optional: POST incident payloads here (Slack incoming webhook, custom SIEM, etc.)
    ALERT_WEBHOOK_URL: str = Field(default="", description="Generic webhook URL for incident alerts (leave empty to disable)")
    SLACK_WEBHOOK_URL: str = Field(default="", description="Slack incoming webhook URL for security alerts (leave empty to disable)")

    # ─────────────────────────────────────────────────────────────
    # 🌐 CORS
    # ─────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins.
    # Dev defaults to localhost Vite/React ports.
    # In production set to your actual domain, e.g.:
    #   ALLOWED_ORIGINS=https://app.yourcompany.com
    ALLOWED_ORIGINS: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000",
        description="Comma-separated CORS allowed origins",
    )

    # ─────────────────────────────────────────────────────────────
    # 🔭 Observability
    # ─────────────────────────────────────────────────────────────
    # Leave empty to disable distributed tracing (safe for dev/Docker without a collector)
    OTLP_ENDPOINT: str = Field(default="")

    @field_validator("JWT_SECRET_KEY", "INTERNAL_SECRET", "GROQ_API_KEY")
    @classmethod
    def _must_not_be_empty(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} must not be empty")
        return v

    # ─────────────────────────────────────────────────────────────
    # 🚦 Rate Limits
    # ─────────────────────────────────────────────────────────────

    GLOBAL_RATE_LIMIT: int = Field(default=100_000)
    IP_RATE_LIMIT: int = Field(default=10_000)
    TENANT_RATE_LIMIT: int = Field(default=10_000)
    AGENT_RATE_LIMIT: int = Field(default=10_000)
    TOKEN_RATE_LIMIT: int = Field(default=10_000)

    # ─────────────────────────────────────────────────────────────
    # 🚧 Gateway hot-path limits (was hardcoded in middleware.py)
    # ─────────────────────────────────────────────────────────────
    MAX_CONCURRENT_EXECUTION: int = Field(
        default=500,
        description="Backpressure semaphore on /execute path",
    )
    MAX_PAYLOAD_BYTES: int = Field(
        default=10_000,
        description="Absolute payload size cap at gateway ingress (bytes)",
    )

    # ─────────────────────────────────────────────────────────────
    # ⏱️ Decision service per-call HTTP timeouts (was hardcoded)
    # ─────────────────────────────────────────────────────────────
    DECISION_REGISTRY_TIMEOUT_CONNECT: float = Field(default=0.3)
    DECISION_REGISTRY_TIMEOUT_READ: float = Field(default=0.6)
    DECISION_REGISTRY_TIMEOUT_WRITE: float = Field(default=0.3)
    DECISION_REGISTRY_TIMEOUT_POOL: float = Field(default=0.3)

    DECISION_GATHER_TIMEOUT_CONNECT: float = Field(default=0.3)
    DECISION_GATHER_TIMEOUT_READ: float = Field(default=0.8)
    DECISION_GATHER_TIMEOUT_WRITE: float = Field(default=0.3)
    DECISION_GATHER_TIMEOUT_POOL: float = Field(default=0.3)

    DECISION_GATHER_TOTAL_TIMEOUT: float = Field(
        default=1.5,
        description=(
            "asyncio.wait_for cap on the parallel policy+behavior fan-out. "
            "Was 1.0s — at scale that left behavior with as little as 0.4s "
            "after policy round-tripped, so the behavior call frequently "
            "timed out and fell through to fail-closed risk=0.5 even though "
            "the service was healthy. 1.5s sits comfortably under the "
            "gateway's 2.0s SLA budget."
        ),
    )

    # Sprint 2 perf: per-phase TCP connect deadline for every downstream
    # HTTP call made via ResilientClient. The previous behaviour set
    # connect to half the overall timeout (1s for the gateway's 2s
    # default), which meant brownouts where a downstream wasn't
    # accepting connections consumed ~1s on every retry before failing
    # fast. 100ms is generous for LAN-co-located services (<2ms
    # typical) and bounds the worst case at <1s end-to-end after
    # retries+backoff. Tune via env var without a code change.
    RESILIENT_CONNECT_TIMEOUT_MS: int = Field(
        default=100,
        description="Per-phase TCP connect timeout (ms) for ResilientClient. "
        "100ms = LAN-co-located default. Override per environment.",
    )


# Singleton instance
settings = ACPSettings()
