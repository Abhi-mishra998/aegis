"""sprint 6: shadow policy + version + shadow_decisions + online_eval_configs

Revision ID: y0a1b2c3d4e5
Revises: x8y9z0a1b2c3
Create Date: 2026-06-13 13:30:00.000000

Sprint 6 — Shadow Mode & Online Evaluation. Adds four tables under the
audit DB. The hot path NEVER reads from any of these; shadow evaluation
is fire-and-forget on the gateway side and writes via the same async
SessionLocal as the other audit-service async workers.

* shadow_policies            — candidate policy in draft|shadow|enforce
                               mode, optional per-agent scope
* shadow_policy_versions     — append-only history for rollback
* shadow_decisions           — would-have-decided per /execute
* online_eval_configs        — per-tenant sampler config + drift threshold
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "y0a1b2c3d4e5"
down_revision: str | None = "x8y9z0a1b2c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shadow_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mode", sa.String(20), nullable=False, server_default="draft"),
        sa.Column(
            "rules_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "sample_rate", sa.Float(), nullable=False, server_default="1.0"
        ),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_shadow_policies_tenant_id", "shadow_policies", ["tenant_id"]
    )
    op.create_index(
        "ix_shadow_policies_agent_id", "shadow_policies", ["agent_id"]
    )
    op.create_index(
        "ix_shadow_policies_mode", "shadow_policies", ["mode"]
    )
    op.create_index(
        "ix_shadow_policies_tenant_mode",
        "shadow_policies",
        ["tenant_id", "mode"],
    )
    op.create_index(
        "ix_shadow_policies_tenant_agent",
        "shadow_policies",
        ["tenant_id", "agent_id"],
    )

    op.create_table(
        "shadow_policy_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("change_kind", sa.String(20), nullable=False),
        sa.Column("mode_before", sa.String(20), nullable=True),
        sa.Column("mode_after", sa.String(20), nullable=False),
        sa.Column(
            "rules_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("changed_by", sa.String(255), nullable=True),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_shadow_policy_versions_policy_id",
        "shadow_policy_versions",
        ["policy_id"],
    )
    op.create_index(
        "ix_shadow_policy_versions_tenant_id",
        "shadow_policy_versions",
        ["tenant_id"],
    )
    op.create_index(
        "uq_shadow_policy_version",
        "shadow_policy_versions",
        ["policy_id", "version"],
        unique=True,
    )

    op.create_table(
        "shadow_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("policy_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "policy_version", sa.Integer(), nullable=False, server_default="1"
        ),
        sa.Column("request_id", sa.String(80), nullable=True),
        sa.Column("audit_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("tool", sa.String(255), nullable=True),
        sa.Column("real_action", sa.String(20), nullable=False),
        sa.Column("shadow_action", sa.String(20), nullable=False),
        sa.Column("matched_rule_index", sa.Integer(), nullable=True),
        sa.Column("matched_rule_description", sa.String(255), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=True),
        sa.Column("risk_score", sa.Float(), nullable=True),
        sa.Column(
            "eval_latency_ms", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_shadow_decisions_tenant_id", "shadow_decisions", ["tenant_id"]
    )
    op.create_index(
        "ix_shadow_decisions_agent_id", "shadow_decisions", ["agent_id"]
    )
    op.create_index(
        "ix_shadow_decisions_policy_id", "shadow_decisions", ["policy_id"]
    )
    op.create_index(
        "ix_shadow_decisions_request_id", "shadow_decisions", ["request_id"]
    )
    op.create_index(
        "ix_shadow_decisions_audit_id", "shadow_decisions", ["audit_id"]
    )
    op.create_index(
        "ix_shadow_decisions_policy_created",
        "shadow_decisions",
        ["policy_id", "created_at"],
    )
    op.create_index(
        "ix_shadow_decisions_tenant_created",
        "shadow_decisions",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "ix_shadow_decisions_drift",
        "shadow_decisions",
        ["policy_id", "real_action", "shadow_action"],
    )

    op.create_table(
        "online_eval_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
            unique=True,
        ),
        sa.Column(
            "enabled", sa.SmallInteger(), nullable=False, server_default="1"
        ),
        sa.Column(
            "sample_rate", sa.Float(), nullable=False, server_default="0.05"
        ),
        sa.Column(
            "fp_threshold", sa.Float(), nullable=False, server_default="0.05"
        ),
        sa.Column(
            "poll_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default="900",
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_online_eval_configs_tenant_id",
        "online_eval_configs",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_online_eval_configs_tenant_id", table_name="online_eval_configs"
    )
    op.drop_table("online_eval_configs")

    op.drop_index("ix_shadow_decisions_drift", table_name="shadow_decisions")
    op.drop_index(
        "ix_shadow_decisions_tenant_created", table_name="shadow_decisions"
    )
    op.drop_index(
        "ix_shadow_decisions_policy_created", table_name="shadow_decisions"
    )
    op.drop_index(
        "ix_shadow_decisions_audit_id", table_name="shadow_decisions"
    )
    op.drop_index(
        "ix_shadow_decisions_request_id", table_name="shadow_decisions"
    )
    op.drop_index(
        "ix_shadow_decisions_policy_id", table_name="shadow_decisions"
    )
    op.drop_index(
        "ix_shadow_decisions_agent_id", table_name="shadow_decisions"
    )
    op.drop_index(
        "ix_shadow_decisions_tenant_id", table_name="shadow_decisions"
    )
    op.drop_table("shadow_decisions")

    op.drop_index(
        "uq_shadow_policy_version", table_name="shadow_policy_versions"
    )
    op.drop_index(
        "ix_shadow_policy_versions_tenant_id",
        table_name="shadow_policy_versions",
    )
    op.drop_index(
        "ix_shadow_policy_versions_policy_id",
        table_name="shadow_policy_versions",
    )
    op.drop_table("shadow_policy_versions")

    op.drop_index(
        "ix_shadow_policies_tenant_agent", table_name="shadow_policies"
    )
    op.drop_index(
        "ix_shadow_policies_tenant_mode", table_name="shadow_policies"
    )
    op.drop_index("ix_shadow_policies_mode", table_name="shadow_policies")
    op.drop_index("ix_shadow_policies_agent_id", table_name="shadow_policies")
    op.drop_index("ix_shadow_policies_tenant_id", table_name="shadow_policies")
    op.drop_table("shadow_policies")
