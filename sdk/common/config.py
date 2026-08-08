"""
ACP Shared Configuration
========================
Single source of truth for all service settings.
Supports both Docker and Local environments cleanly.
"""

from __future__ import annotations

import os

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sprint 25 A4 — kept at MODULE level (not inside ACPSettings) because
# leading-underscore class attributes in Pydantic v2 BaseSettings get
# treated as `ModelPrivateAttr` and become non-iterable proxies.
# Discovered via the 2026-06-26 prod deploy crash: every service that
# imported settings.py died at import with TypeError on the iteration
# inside the validator below.
_PROD_REQUIRED_SERVICE_URLS: tuple[str, ...] = (
    "REGISTRY_SERVICE_URL", "IDENTITY_SERVICE_URL", "POLICY_SERVICE_URL",
    "AUDIT_SERVICE_URL", "API_SERVICE_URL", "BEHAVIOR_SERVICE_URL",
    "DECISION_SERVICE_URL", "USAGE_SERVICE_URL", "INSIGHT_SERVICE_URL",
    "FORENSICS_SERVICE_URL", "IDENTITY_GRAPH_SERVICE_URL",
    "FLIGHT_RECORDER_SERVICE_URL", "AUTONOMY_SERVICE_URL",
)


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

    # Sprint 17 — Aegis for Teams. The corporate Anthropic API key that
    # the /v1/messages proxy uses upstream when forwarding employee
    # virtual-key calls to api.anthropic.com. Empty string disables the
    # proxy (the endpoint returns 503 with a configuration message).
    # For multi-tenant SaaS this will move to a per-tenant encrypted
    # column in a follow-on sprint; the env-var path is the single-
    # tenant deployment story.
    UPSTREAM_ANTHROPIC_KEY: str = Field(
        default="",
        description=(
            "Corporate Anthropic API key used by the /v1/messages "
            "Aegis-for-Teams proxy. Empty disables the proxy."
        ),
    )
    # Sprint 22 — corporate OpenAI key used by the
    # /v1/chat/completions Aegis-for-Teams proxy.
    UPSTREAM_OPENAI_KEY: str = Field(
        default="",
        description=(
            "Corporate OpenAI API key used by the /v1/chat/completions "
            "Aegis-for-Teams proxy. Empty disables the proxy."
        ),
    )

    # Sprint 17 — separate URL for the api-service database (where
    # api_keys lives). Falls back to DATABASE_URL when unset so legacy
    # single-DB deployments keep working.
    API_DATABASE_URL: str = Field(
        default="",
        description=(
            "Optional: connection string for the api-service database. "
            "If empty, falls back to DATABASE_URL."
        ),
    )

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
    # N11 fix (2026-06-21) — Prometheus /metrics scrape gate, independent
    # of INTERNAL_SECRET. Prometheus runs in the docker network without a
    # mesh ES256 keypair, so it can't speak the new mesh JWT path. Keep
    # the scrape behind its OWN secret so a leak of INTERNAL_SECRET no
    # longer hands an attacker a path to scrape tenant-labelled gauges
    # (AUTH_FAILURES_TOTAL by role, TENANT_ISOLATION_VIOLATIONS_TOTAL, …).
    # Rotates independently; only Prometheus + the gateway's middleware
    # need to know it.
    PROMETHEUS_SCRAPE_SECRET: str = Field(
        default="",
        description=(
            "Dedicated secret for Prometheus /metrics scrape. Independent of "
            "INTERNAL_SECRET so a leak of the mesh secret cannot scrape tenant "
            "metrics. Empty string ⇒ /metrics requires X-Mesh-Token (no legacy lane)."
        ),
    )

    # Sprint EI-9 (2026-06-20) — Cloudflare Turnstile bot-defence on
    # /demo/spawn-workspace. If unset (local dev), the verifier is bypassed
    # and the spawn endpoint behaves exactly as before. If set, the spawn
    # handler requires a valid cf-turnstile-response token in the request
    # body and returns 403 on missing/invalid.
    TURNSTILE_SECRET_KEY: str = Field(
        default="",
        description="Cloudflare Turnstile secret key. Empty = verifier bypassed (local dev)."
    )
    TURNSTILE_VERIFY_URL: str = Field(
        default="https://challenges.cloudflare.com/turnstile/v0/siteverify",
        description="Cloudflare siteverify endpoint. Override only for the synthetic test fixture."
    )

    OPA_URL: str = Field(
        default="http://acp_opa:8181"
    )
    OPA_FAIL_MODE: str = Field(
        default="closed",
        description="'closed' = deny on OPA failure (default, safe); 'open' = allow on OPA failure (use only for dev/staging)"
    )

    @field_validator("OPA_FAIL_MODE")
    @classmethod
    def _guard_opa_fail_mode(cls, v: str, info) -> str:
        """S6 (audit P1-5) + SEC-2026-07-31 (H9): OPA_FAIL_MODE=open is a
        total-policy-bypass foot-gun. The three fail-open branches at
        ``services/policy/opa_client.py:91, 99, 163`` turn OPA outages into
        blanket ALLOW when this mode is ``open``. Prod always refuses
        ``open`` — and now, non-prod environments require an explicit
        second-factor env ``ACK_UNSAFE_FAIL_OPEN=1`` before ``open`` is
        accepted, so a staging→prod promo can't quietly carry the bit.
        """
        v_lower = v.strip().lower()
        if v_lower not in ("open", "closed"):
            raise ValueError(
                f"OPA_FAIL_MODE must be 'open' or 'closed'; got {v!r}."
            )
        env = (
            (info.data.get("ENVIRONMENT") if info and info.data else None)
            or os.environ.get("ENVIRONMENT")
            or ""
        ).strip().lower()
        if v_lower == "open" and env == "prod":
            raise ValueError(
                "OPA_FAIL_MODE='open' is refused in prod: an OPA outage "
                "would silently ALLOW every consequential action (policy "
                "bypass). Set OPA_FAIL_MODE=closed for prod, or set "
                "ENVIRONMENT to a non-prod value (development/staging) "
                "if the fail-open is intentional for local work."
            )
        if v_lower == "open":
            ack = (os.environ.get("ACK_UNSAFE_FAIL_OPEN", "") or "").strip()
            if ack not in ("1", "true", "yes"):
                raise ValueError(
                    "OPA_FAIL_MODE='open' also requires ACK_UNSAFE_FAIL_OPEN=1 "
                    "in the environment. This double-opt-in stops a staging→prod "
                    "promotion from silently carrying an OPA-bypass posture "
                    "into production. Set OPA_FAIL_MODE=closed to remove the "
                    "warning, or export ACK_UNSAFE_FAIL_OPEN=1 for the (dev) "
                    "shell where you actually want fail-open."
                )
        return v_lower

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
    # ATF v3.2 §6 Execution Witness
    WITNESS_SERVICE_URL: str = Field(default="http://localhost:8017")

    # ATF v3.2 §4.4 — Aegis Profile issuance quota per tenant. Contractual
    # ceiling on concurrent minted profiles. Blocks past this. Alerts on 95%.
    TENANT_PROFILE_QUOTA_DEFAULT: int = Field(
        default=1000,
        description="Default per-tenant cap on concurrent Aegis Profiles. C2 audit event on 95% headroom, 429 past ceiling.",
    )

    # ATF v3.2 §4.2 SCIM adapter — customer's SCIM directory for
    # human_responsible reconciliation. Blank disables the reconciler.
    SCIM_BASE_URL: str = Field(
        default="",
        description="Customer SCIM 2.0 endpoint base URL, e.g. https://acme.scim.example/scim/v2",
    )
    SCIM_BEARER_TOKEN: str = Field(
        default="",
        description="Bearer token for the SCIM endpoint. Rotate via ops.",
    )
    SCIM_RECONCILE_TIMEOUT_SECONDS: float = Field(
        default=5.0,
        description="Per-request SCIM timeout. A slow directory MUST NOT stall reconciliation.",
    )

    # Sprint 25 A4 — fail-fast if a prod deploy still has localhost service URLs.
    # The defaults above let dev/CI/examples work without env wiring; the
    # validator below catches the silent-misroute bug where a missing env var
    # in production falls back to http://localhost:800X and the service
    # happily talks to itself instead of the intended cluster peer.
    #
    # Sprint 25 hotfix (2026-06-26) — opt-in via AEGIS_VALIDATE_SERVICE_URLS=1.
    # Without the opt-in, this validator is a no-op: prod deploys that
    # haven't yet wired all 13 service URLs into docker-compose keep working
    # exactly as before. Set the flag once compose has every URL set + the
    # ASG/k8s rollout has been tested.
    @model_validator(mode="after")
    def _no_localhost_urls_in_prod(self) -> ACPSettings:
        if self.ENVIRONMENT != "production":
            return self
        if os.environ.get("AEGIS_VALIDATE_SERVICE_URLS", "0") != "1":
            return self
        bad = [
            f for f in _PROD_REQUIRED_SERVICE_URLS
            if getattr(self, f, "").startswith("http://localhost")
        ]
        if bad:
            raise ValueError(
                "ENVIRONMENT=production + AEGIS_VALIDATE_SERVICE_URLS=1 but "
                "these service URLs still point at localhost (probably missing "
                f"env vars): {', '.join(bad)}"
            )
        return self

    # Optional: POST incident payloads here (Slack incoming webhook, custom SIEM, etc.)
    ALERT_WEBHOOK_URL: str = Field(default="", description="Generic webhook URL for incident alerts (leave empty to disable)")
    SLACK_WEBHOOK_URL: str = Field(default="", description="Slack incoming webhook URL for security alerts (leave empty to disable)")

    # ─────────────────────────────────────────────────────────────
    # 📡 SIEM Integration (optional — leave SIEM_TARGET="" to disable)
    # ─────────────────────────────────────────────────────────────
    # Sprint 2b extends targets from {splunk, datadog} to also include
    # elastic, sentinel, chronicle. Credentials can come from env (legacy)
    # or AWS SSM Parameter Store at /aegis-siem/<target>/*.
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
    # 🔑 Auth
    # ─────────────────────────────────────────────────────────────
    # First-party HS256 access tokens (issued by services/identity/token_service.py)
    # cover the entire OSS flow: /auth/register, /auth/token, /auth/password/*.
    # External IdPs (SPIFFE / Entra / Okta) plug in via the ATF §4.2 adapters
    # below — set a tenant's SSM params to opt that adapter in.
    ACP_AUTH_PROVIDER: str = Field(
        default="legacy",
        description="Reserved for future providers. Only 'legacy' is supported.",
    )
    # ─────────────────────────────────────────────────────────────
    # ATF v3.2 §4.2 — external IdP acceptance. All optional; a blank
    # value disables that adapter. The gateway auth dispatcher tries
    # each configured adapter in order of specificity (SPIFFE > Entra
    # > Okta > legacy) and fails-closed with the uniform
    # "Unauthorized" body if no adapter accepts.
    # ─────────────────────────────────────────────────────────────
    SPIFFE_TRUST_DOMAIN: str = Field(
        default="",
        description="Expected SPIFFE trust domain (e.g. 'acme.example'). Blank disables SPIFFE acceptance.",
    )
    SPIFFE_TRUST_BUNDLE_JSON: str = Field(
        default="",
        description="JWKS-shaped trust bundle for the SPIFFE trust domain, JSON string. Rotated by SPIRE or operator.",
    )
    SPIFFE_AUDIENCE: str = Field(
        default="",
        description="Expected `aud` on incoming SVIDs. Blank = verify_aud disabled (per-workload SVIDs may lack aud).",
    )

    ENTRA_TENANT_ID: str = Field(
        default="",
        description="Microsoft Entra tenant GUID. Blank disables Entra Agent ID acceptance.",
    )
    ENTRA_AUDIENCE: str = Field(
        default="",
        description="Expected `aud` on incoming Entra tokens (typically the Aegis app ID).",
    )
    ENTRA_JWKS_CACHE_SECONDS: int = Field(
        default=3600,
        description="Entra JWKS cache TTL. Entra rotates infrequently; 1h balances safety + load.",
    )

    OKTA_ISSUER: str = Field(
        default="",
        description="Full Okta issuer URL (https://<tenant>.okta.com/oauth2/default or an XAA auth server). Blank disables Okta acceptance.",
    )
    OKTA_AUDIENCE: str = Field(
        default="",
        description="Expected `aud` on incoming Okta tokens.",
    )
    OKTA_JWKS_CACHE_SECONDS: int = Field(
        default=3600,
        description="Okta JWKS cache TTL. Okta rotates keys periodically; 1h is safe.",
    )

    @field_validator("ACP_AUTH_PROVIDER")
    @classmethod
    def _allowed_auth_provider(cls, v: str) -> str:
        # `clerk` and `both` are accepted as aliases for `legacy` so existing
        # deployment configs don't need to be touched before a redeploy.
        if v in ("clerk", "both"):
            return "legacy"
        if v != "legacy":
            raise ValueError(
                f"ACP_AUTH_PROVIDER must be 'legacy' (only supported value); got {v!r}."
            )
        return v


# Singleton instance
settings = ACPSettings()
