from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Integer, Numeric, String, text
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from sdk.common.db import Base, IdMixin, OrgMixin, TenantMixin, TimestampMixin

# =========================
# ENUMS
# =========================


class CredentialStatus(StrEnum):
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


class UserRole(StrEnum):
    """
    Persisted user-role vocabulary.

    The original 5 values (ADMIN/SECURITY/AUDITOR/VIEWER/AGENT) predate the
    Sprint-1 Real-SaaS effort. The next 4 (OWNER/SECURITY_ANALYST/DEVELOPER/
    READ_ONLY) were added on top to model self-serve workspace permissions:

      - OWNER            — billing + can-delete-workspace; one per workspace.
      - ADMIN            — manage members, policies, integrations; many.
      - SECURITY_ANALYST — read all incidents + audit; manage policies.
      - DEVELOPER        — read incidents for their agents; create agents.
      - READ_ONLY        — read dashboard + audit; no writes.

    The legacy values (SECURITY, AUDITOR, VIEWER, AGENT) stay in the enum for
    back-compat with rows written before the migration; new signups always
    use the OWNER/ADMIN/... vocabulary. The verify_role gateway middleware
    accepts either pool.
    """

    # Legacy (do not use for new code)
    ADMIN = "ADMIN"
    SECURITY = "SECURITY"
    AUDITOR = "AUDITOR"
    VIEWER = "VIEWER"
    AGENT = "AGENT"

    # Real-SaaS canonical roles (Sprint 1)
    OWNER = "OWNER"
    SECURITY_ANALYST = "SECURITY_ANALYST"
    DEVELOPER = "DEVELOPER"
    READ_ONLY = "READ_ONLY"


# Canonical role vocabulary lives in sdk.common.roles so the gateway's
# verify_role middleware and the identity writer share one source of truth.
from sdk.common.roles import LEGACY_ROLE_TO_CANONICAL, Role, canonical_role  # noqa: E402,F401


# Sprint 1 shadow-mode default: every new workspace runs in observe-only
# mode for 14 days before its first deny/escalate actually blocks the
# customer's agent traffic. Centralised so /signup, the Clerk webhook
# receiver, and the alembic server_default all stay in lockstep.
SHADOW_MODE_DEFAULT_DAYS = 14


def default_shadow_mode_until() -> datetime:
    """Default value for Tenant.shadow_mode_until on new rows."""
    return datetime.utcnow() + timedelta(days=SHADOW_MODE_DEFAULT_DAYS)


class TenantTier(StrEnum):
    BASIC = "basic"
    PRO = "pro"
    ENTERPRISE = "enterprise"


class DegradedModePolicy(StrEnum):
    """Per-tenant policy for decision behavior when the behavior firewall
    service is unreachable. See
    services/identity/alembic/versions/b8e9f0a1c2d3_add_degraded_mode_policy.py."""

    BLOCK_HIGH_RISK = "block_high_risk"
    BLOCK_ALL = "block_all"
    ALLOW_WITH_AUDIT = "allow_with_audit"


# =========================
# MODELS
# =========================


class Organization(Base, IdMixin, TimestampMixin):
    """
    Top-level org entity. One org owns one or more tenants.
    Created implicitly when the first admin user registers, or by the
    Clerk webhook when an `organization.created` event arrives.
    """

    __tablename__ = "organizations"

    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Clerk linkage — set when the org was either created by a Clerk webhook
    # OR provisioned by /auth/clerk/provision after a self-serve signup. NULL
    # on legacy orgs created before Sprint 1.
    clerk_org_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True,
    )


class Tenant(Base, OrgMixin, IdMixin, TimestampMixin):
    """
    Tenant = workspace within an org (e.g. dev / staging / prod).
    Rate limits and tier are enforced at this level.
    """

    __tablename__ = "tenants"

    # tenant_id here IS the ACP tenant_id used in all other tables
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, unique=True, index=True
    )

    name: Mapped[str] = mapped_column(String(255), nullable=False)

    tier: Mapped[TenantTier] = mapped_column(
        SQLEnum(TenantTier, name="tenant_tier_enum", values_callable=lambda obj: [e.value for e in obj]),
        default=TenantTier.BASIC,
        nullable=False,
        index=True,
    )

    # Requests per minute — 0 means use tier defaults
    rpm_limit: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Sprint 3.2 — per-tenant quota: token-bucket (rps + burst) +
    # daily/monthly request caps. rps_limit + burst feed the token bucket;
    # daily/monthly are simple INCR counters in Redis (UTC-day / UTC-month
    # keyed). monthly_request_cap=NULL means no monthly ceiling.
    requests_per_second: Mapped[int] = mapped_column(
        Integer, default=50, server_default="50", nullable=False,
    )
    burst: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False,
    )
    daily_request_cap: Mapped[int] = mapped_column(
        Integer, default=1_000_000, server_default="1000000", nullable=False,
    )
    monthly_request_cap: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )

    # Sprint 3.5 — daily inference dollar cap (USD). NULL means no cap.
    # Per-agent caps live in Redis as a hot-config override
    # (`acp:agent_cost_cap:{agent_id}` = USD as a string) so operators can
    # set them without a DB migration.
    daily_inference_cost_cap_usd: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True,
    )

    degraded_mode_policy: Mapped[DegradedModePolicy] = mapped_column(
        SQLEnum(
            DegradedModePolicy,
            name="degraded_mode_policy_enum",
            values_callable=lambda obj: [e.value for e in obj],
        ),
        default=DegradedModePolicy.BLOCK_HIGH_RISK,
        nullable=False,
    )

    # Sprint 1 — Shadow mode default. Every freshly-created workspace runs in
    # observe-only mode for 14 days. Deny/escalate decisions still fire in
    # the engine but the gateway middleware downgrades them to an audited
    # `would_have_blocked` event, leaving the customer's agent traffic
    # untouched. Owners can extend or exit early via POST /workspace/exit-shadow-mode.
    shadow_mode_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        server_default=text("now() + interval '14 days'"),
    )

    # Sprint 8 — Per-resource-kind dollar weights for the Blast-Radius
    # dollar formula. Operator-set via PATCH /workspace/system-values
    # (OWNER role). Defaults to `{}` — pre-Sprint-8 tenants then surface
    # a zero dollar_estimate and the BlastRadiusCard falls back to the
    # criticality_score pill.
    #
    # Example shape: {"table": 50000, "api": 100000, "secret": 25000}.
    from sqlalchemy.dialects.postgresql import JSONB  # noqa: E402,PLC0415
    system_values: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        default=dict,
    )

    # Sprint 21 — Slack approvals. When set, the /v1/messages escalation
    # path posts a block-kit card to `slack_webhook_url` with two
    # HMAC-signed links (Approve / Reject). The link callbacks hit
    # /slack/approve/{id} + /slack/reject/{id}, verify the signature
    # against `slack_approval_secret`, then land an
    # `human_override_events` row exactly like the in-app inbox
    # button would. NULL on both columns disables the feature.
    slack_webhook_url: Mapped[str | None] = mapped_column(
        String(512), nullable=True,
    )
    slack_approval_secret: Mapped[str | None] = mapped_column(
        String(128), nullable=True,
    )

    # Sprint S2 (2026-06-19) — Slack OAuth columns. Populated by the
    # /sso/slack/initiate + /sso/slack/callback flow when the operator
    # clicks "Connect Slack" instead of pasting a webhook by hand. The
    # callback ALSO sets slack_webhook_url above so the legacy approval
    # path keeps firing — these columns are additive.
    slack_bot_token: Mapped[str | None] = mapped_column(
        String(256), nullable=True,
    )
    slack_workspace_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )
    slack_channel_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True,
    )

    # Sprint 23 — Compliance Policy Packs (SOC2 / PCI / HIPAA / Finance
    # / DevOps). List of pack IDs the tenant has enabled — the gateway
    # consults this on every /v1/messages + /v1/chat/completions to
    # extend the base escalation pattern set without redeploying the
    # policy engine. Default empty array means only the founder's base
    # rules (wire $100k, kubectl prod, etc.) apply.
    enabled_policy_packs: Mapped[list] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'[]'::jsonb"),
        default=list,
    )

    # Sprint S4 (2026-06-19) — Demo workspace lifecycle. is_demo=true marks
    # a tenant as a sandbox spawned by POST /demo/spawn-workspace for
    # cold-start prospects. demo_expires_at is the cleanup deadline; the
    # operator sweeps these via POST /demo/cleanup-expired (or a 24h cron)
    # and hard-deletes the identity row + cascades agent/audit/usage.
    is_demo: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False,
    )
    demo_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )


class AgentCredential(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    """Stores hashed secrets + status for agent authentication."""

    __tablename__ = "agent_credentials"

    agent_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        unique=True,
        index=True,
        nullable=False,
    )

    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    status: Mapped[CredentialStatus] = mapped_column(
        SQLEnum(CredentialStatus, name="credential_status_enum"),
        default=CredentialStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class User(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    """Represents a human administrator or viewer of the ACP Dashboard."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str] = mapped_column(String(255), nullable=True)

    role: Mapped[UserRole] = mapped_column(
        SQLEnum(UserRole, name="user_role_enum"),
        default=UserRole.VIEWER,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    last_login: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=True)

    # Clerk linkage — set when the user was either created by a Clerk webhook
    # OR self-served via the Clerk SignUp component. NULL on legacy users
    # (admin@acp.local, brutal-test agents, etc.) created before Sprint 1.
    clerk_user_id: Mapped[str | None] = mapped_column(
        String(64), unique=True, nullable=True, index=True,
    )

    # Sprint S5 (2026-06-19) — Replace free-text department with a Team
    # FK. Null = unassigned (legacy users + signup-pre-team-assignment).
    # ON DELETE SET NULL on the Team side: deleting a team un-assigns
    # its members but does not cascade-delete user rows.
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )


class Team(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    """Sprint S5 (2026-06-19) — Hierarchical Teams.

    Replaces the free-text `department` field with a formal tree
    structure: every team has an optional parent_team_id (self-FK)
    and an optional manager_user_id. The Team page rolls spend +
    harmful-blocked counts up the parent chain so a CFO can see All,
    Engineering Lead can see only Engineering + children.

    Per-team budget caps live in `daily_budget_usd_cap` /
    `monthly_budget_usd_cap` — the gateway consults them on every
    /v1/messages alongside the per-tenant cap so a runaway agent in
    one team can't burn the whole tenant budget.
    """

    __tablename__ = "teams"

    name: Mapped[str] = mapped_column(String(128), nullable=False)
    parent_team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True,
    )
    manager_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )

    # Per-team budget caps (in USD). Null = inherit from tenant cap.
    daily_budget_usd_cap: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )
    monthly_budget_usd_cap: Mapped[int | None] = mapped_column(
        Integer, nullable=True,
    )


class ScimToken(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    """Sprint EI-3 (2026-06-20) — bearer token used by Okta SCIM provisioning.

    Separate from APIKey because:
      * scope is /scim/v2/* only (not /v1/messages, /execute, etc.)
      * the issuance flow is OWNER-gated and returns the plaintext exactly
        once; nothing else (no role grants, no expiry by default)
      * Okta sends one bearer for every provisioning call, regardless of
        Okta admin user — so we deliberately do NOT bind a role here

    The token is sha256-hashed at rest with a per-tenant prefix on the raw
    value (``scim_<16 base32 chars>``) for log-friendly identification.
    """

    __tablename__ = "scim_tokens"

    label: Mapped[str] = mapped_column(String(128), nullable=False)
    token_hash: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True,
    )
    token_prefix: Mapped[str] = mapped_column(String(24), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True,
    )


class JiraIntegration(Base, OrgMixin, TenantMixin, IdMixin, TimestampMixin):
    """Sprint EI-2 (2026-06-20) — per-tenant Jira Cloud ITSM integration.

    One row per tenant (uniqueness enforced at the application layer by the
    upsert helper in services/identity/router.py). ``api_token`` holds the
    Atlassian API token as the same disk-KMS-protected String column that
    ``Tenant.slack_bot_token`` uses; the REST surface never returns the raw
    value — only ``has_api_token: bool``.
    """

    __tablename__ = "jira_integrations"

    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    project_key: Mapped[str] = mapped_column(String(32), nullable=False)
    account_email: Mapped[str] = mapped_column(String(255), nullable=False)
    api_token: Mapped[str] = mapped_column(String(512), nullable=False)
    default_issue_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="Bug",
        server_default=text("'Bug'"),
    )
    default_priority: Mapped[str | None] = mapped_column(String(32), nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )
    auto_create_on_incident: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true"),
    )


# ---------------------------------------------------------------------------
# HARDENED INVARIANTS (SQLAlchemy Events)
# ---------------------------------------------------------------------------

from sqlalchemy import event


@event.listens_for(User, "before_insert")
@event.listens_for(AgentCredential, "before_insert")
@event.listens_for(Tenant, "before_insert")
def enforce_org_id_invariant(mapper, connection, target) -> None:
    """
    Final defensive check before flush:
    If org_id is missing, it MUST default to tenant_id.
    """
    if hasattr(target, "org_id") and target.org_id is None:
        if hasattr(target, "tenant_id") and target.tenant_id is not None:
            target.org_id = target.tenant_id
        elif isinstance(target, Tenant):
            # For Tenant model, org_id must match its own tenant_id
            target.org_id = target.tenant_id
