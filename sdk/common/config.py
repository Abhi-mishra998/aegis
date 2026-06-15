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

    @field_validator("JWT_ALGORITHM")
    @classmethod
    def _allowed_jwt_algorithm(cls, v: str) -> str:
        allowed = {"HS256", "HS384", "HS512", "RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
        if v not in allowed:
            raise ValueError(
                f"JWT_ALGORITHM must be one of {sorted(allowed)}; got {v!r}. "
                f"Refusing to start — 'none' or unknown algorithms disable signature verification."
            )
        return v

    # ─────────────────────────────────────────────────────────────
    # 🌐 External Services
    # ─────────────────────────────────────────────────────────────
    GROQ_API_KEY: str = Field(
        default="",
        description="Groq API key for the insight/groq_worker services. Leave empty to disable Groq features."
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
    MESH_JWT_SECRET: str = Field(
        default="",
        description="Signing key for service mesh JWTs. Falls back to INTERNAL_SECRET if empty."
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
    # 📡 SIEM Integration (optional — leave SIEM_TARGET="" to disable)
    # ─────────────────────────────────────────────────────────────
    # Sprint 2b extends targets from {splunk, datadog} to also include
    # elastic, sentinel, chronicle. Credentials can come from env (legacy)
    # or AWS SSM Parameter Store at /aegis-siem/<target>/* — matching the
    # existing /aegis-voice-guide/* convention.
    SIEM_TARGET: str = Field(default="", description="SIEM target: '' | 'splunk' | 'datadog' | 'elastic' | 'sentinel' | 'chronicle'")
    SIEM_CRED_SOURCE: str = Field(default="env", description="SIEM credential source: 'env' (default) | 'ssm'")
    SIEM_SSM_PREFIX: str = Field(default="/aegis-siem", description="SSM Parameter Store prefix when SIEM_CRED_SOURCE=ssm")

    SPLUNK_HEC_URL: str = Field(default="", description="Splunk HEC URL (e.g. https://splunk.example.com:8088/services/collector)")
    SPLUNK_HEC_TOKEN: str = Field(default="", description="Splunk HEC token")
    DATADOG_LOGS_URL: str = Field(default="https://http-intake.logs.datadoghq.com/api/v2/logs", description="Datadog Logs API URL")
    DATADOG_API_KEY: str = Field(default="", description="Datadog API key")

    # Elastic Cloud (Bulk Index API). CLOUD_ID derives the cluster URL.
    # API_KEY is the base64-encoded ``id:key`` pair from Kibana.
    ELASTIC_CLOUD_ID: str = Field(default="", description="Elastic Cloud ID (from Elastic Cloud deployment page)")
    ELASTIC_API_KEY: str = Field(default="", description="Elastic API key (base64-encoded id:key pair)")
    ELASTIC_INDEX: str = Field(default="aegis-audit", description="Elastic index for audit events")

    # Microsoft Sentinel (Log Analytics HTTP Data Collector API).
    SENTINEL_WORKSPACE_ID: str = Field(default="", description="Azure Log Analytics workspace id (UUID)")
    SENTINEL_SHARED_KEY: str = Field(default="", description="Azure Log Analytics shared key (base64)")
    SENTINEL_LOG_TYPE: str = Field(default="AegisAudit", description="Sentinel Log-Type header (custom-log table name)")

    # Google Chronicle (UDM Ingest API). Service-account JSON is the
    # downloaded key-file content. Region selects the endpoint host.
    CHRONICLE_CUSTOMER_ID: str = Field(default="", description="Chronicle customer UUID")
    CHRONICLE_SERVICE_ACCOUNT_JSON: str = Field(default="", description="Chronicle service-account key JSON (full key file content)")
    CHRONICLE_REGION: str = Field(default="us", description="Chronicle region: us | europe | asia-southeast1")

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

    @field_validator("JWT_SECRET_KEY", "INTERNAL_SECRET")
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

    # ─────────────────────────────────────────────────────────────
    # 🤖 Multi-LLM Router (Phase 3)
    # ─────────────────────────────────────────────────────────────

    LLM_PROVIDER: str = Field(
        default="groq",
        description="Primary LLM provider: groq|openai|anthropic|azure_openai",
    )
    LLM_FALLBACK_PROVIDER: str = Field(
        default="",
        description="Fallback provider if primary fails (leave empty to disable)",
    )
    LLM_DAILY_COST_CAP_USD: float = Field(
        default=0.0,
        description="Per-tenant daily LLM cost cap in USD (0=disabled)",
    )

    OPENAI_API_KEY: str = Field(default="", description="OpenAI API key")
    OPENAI_MODEL: str = Field(default="gpt-4o-mini", description="Default OpenAI model")

    ANTHROPIC_API_KEY: str = Field(default="", description="Anthropic API key")
    ANTHROPIC_MODEL: str = Field(
        default="claude-haiku-4-5-20251001",
        description="Default Anthropic model",
    )

    AZURE_OPENAI_ENDPOINT: str = Field(default="", description="Azure OpenAI endpoint URL")
    AZURE_OPENAI_API_KEY: str = Field(default="", description="Azure OpenAI API key")
    AZURE_OPENAI_DEPLOYMENT: str = Field(default="", description="Azure OpenAI deployment name")
    AZURE_OPENAI_API_VERSION: str = Field(
        default="2024-02-01",
        description="Azure OpenAI API version",
    )

    # ─────────────────────────────────────────────────────────────
    # 🔍 Injection Classifier (Phase 2)
    # ─────────────────────────────────────────────────────────────

    INJECTION_USE_MODERATION_API: bool = Field(
        default=False,
        description=(
            "Enable OpenAI moderation API for injection detection "
            "(requires OPENAI_API_KEY)"
        ),
    )

    # ─────────────────────────────────────────────────────────────
    # 🔑 Clerk Auth (Sprint 1 — Real SaaS auth)
    # ─────────────────────────────────────────────────────────────
    # The gateway validates Clerk-issued JWTs (RS256, JWKS-rotated) alongside
    # the existing HS256 self-issued JWTs while ACP_AUTH_PROVIDER=both. The
    # identity service receives Clerk webhooks (user.created, organization.*)
    # and provisions Aegis Org/Tenant/User rows in lockstep.
    ACP_AUTH_PROVIDER: str = Field(
        default="legacy",
        description="Auth backend: 'legacy' (HS256 only), 'clerk' (RS256 only), 'both' (accept either).",
    )
    CLERK_PUBLISHABLE_KEY: str = Field(
        default="",
        description="Clerk publishable key (pk_test_... or pk_live_...). Backend uses it for diagnostics; UI loads it via VITE_CLERK_PUBLISHABLE_KEY.",
    )
    CLERK_SECRET_KEY: str = Field(
        default="",
        description="Clerk secret key (sk_test_... or sk_live_...). Used to call Clerk's Backend API for provisioning + metadata writes.",
    )
    CLERK_FRONTEND_API: str = Field(
        default="",
        description="Clerk frontend API base URL (e.g. https://close-moray-54.clerk.accounts.dev).",
    )
    CLERK_JWKS_URL: str = Field(
        default="",
        description="JWKS endpoint URL — typically {CLERK_FRONTEND_API}/.well-known/jwks.json.",
    )
    CLERK_ISSUER: str = Field(
        default="",
        description="Expected iss claim on Clerk-signed JWTs. Must match CLERK_FRONTEND_API.",
    )
    CLERK_WEBHOOK_SECRET: str = Field(
        default="",
        description="Svix webhook signing secret (whsec_...) for verifying inbound Clerk webhooks.",
    )
    CLERK_JWT_TEMPLATE: str = Field(
        default="aegis",
        description="Clerk JWT template name; the frontend calls getToken({template: this}).",
    )
    CLERK_JWKS_CACHE_SECONDS: int = Field(
        default=3600,
        description="JWKS cache TTL in seconds. Clerk rotates keys infrequently; a 1h cache balances safety and load.",
    )

    @field_validator("ACP_AUTH_PROVIDER")
    @classmethod
    def _allowed_auth_provider(cls, v: str) -> str:
        allowed = {"legacy", "clerk", "both"}
        if v not in allowed:
            raise ValueError(
                f"ACP_AUTH_PROVIDER must be one of {sorted(allowed)}; got {v!r}."
            )
        return v


# Singleton instance
settings = ACPSettings()
