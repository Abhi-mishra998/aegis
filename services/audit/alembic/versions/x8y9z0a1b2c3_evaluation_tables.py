"""sprint 5: evaluation tables — datasets, evaluators, jobs, results

Revision ID: x8y9z0a1b2c3
Revises: v5w6x7y8z9a0
Create Date: 2026-06-13 12:00:00.000000

Sprint 5 — Attack Evaluation Suite. Six tables under the audit DB:

* eval_datasets, eval_dataset_cases     — labelled corpus (attack/benign)
* eval_evaluators                       — named scorer configs
* eval_jobs, eval_job_results           — run instance + per-case outcome
* eval_evaluator_score_snapshots        — daily rollup for trend charts

All tables are tenant_id-scoped (UUID) and follow the audit-service Alembic
pattern (sa.UUID + JSONB + server_default for booleans-as-smallint and json).
The (eval_job_id, case_id) unique index on eval_job_results guarantees
idempotency for the runner's at-least-once retry semantics.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "x8y9z0a1b2c3"
down_revision: str | None = "v5w6x7y8z9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "eval_datasets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False, server_default="mixed"),
        sa.Column("version", sa.String(50), nullable=False, server_default="1"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("case_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_eval_datasets_tenant_id", "eval_datasets", ["tenant_id"])
    op.create_index(
        "ix_eval_datasets_tenant_name", "eval_datasets", ["tenant_id", "name"]
    )

    op.create_table(
        "eval_dataset_cases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_kind", sa.String(20), nullable=False),
        sa.Column("owasp_category", sa.String(20), nullable=False),
        sa.Column("base_id", sa.String(80), nullable=False),
        sa.Column("mutation", sa.String(40), nullable=False, server_default="none"),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
        sa.Column("expected_outcome", sa.String(20), nullable=False),
        sa.Column(
            "expected_findings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_eval_dataset_cases_dataset_id", "eval_dataset_cases", ["dataset_id"]
    )
    op.create_index(
        "ix_eval_dataset_cases_tenant_id", "eval_dataset_cases", ["tenant_id"]
    )
    op.create_index(
        "ix_eval_dataset_cases_base_id", "eval_dataset_cases", ["base_id"]
    )
    op.create_index(
        "ix_eval_dataset_cases_owasp", "eval_dataset_cases", ["owasp_category"]
    )
    op.create_index(
        "ix_eval_cases_dataset_kind",
        "eval_dataset_cases",
        ["dataset_id", "case_kind"],
    )
    op.create_index(
        "ix_eval_cases_dataset_owasp",
        "eval_dataset_cases",
        ["dataset_id", "owasp_category"],
    )

    op.create_table(
        "eval_evaluators",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column(
            "config_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_eval_evaluators_tenant_id", "eval_evaluators", ["tenant_id"])
    op.create_index(
        "ix_evaluators_tenant_name", "eval_evaluators", ["tenant_id", "name"]
    )

    op.create_table(
        "eval_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("dataset_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "evaluator_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("schedule", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("status", sa.String(20), nullable=False, server_default="queued"),
        sa.Column("cases_total", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("cases_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "summary_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(255), nullable=True),
        sa.Column(
            "queued_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_eval_jobs_tenant_id", "eval_jobs", ["tenant_id"])
    op.create_index("ix_eval_jobs_dataset_id", "eval_jobs", ["dataset_id"])
    op.create_index("ix_eval_jobs_status", "eval_jobs", ["status"])
    op.create_index(
        "ix_eval_jobs_tenant_queued", "eval_jobs", ["tenant_id", "queued_at"]
    )
    op.create_index(
        "ix_eval_jobs_status_queued", "eval_jobs", ["status", "queued_at"]
    )

    op.create_table(
        "eval_job_results",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("eval_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("case_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owasp_category", sa.String(20), nullable=False),
        sa.Column("case_kind", sa.String(20), nullable=False),
        sa.Column("expected_outcome", sa.String(20), nullable=False),
        sa.Column("actual_outcome", sa.String(20), nullable=False),
        sa.Column("passed", sa.SmallInteger(), nullable=False),
        sa.Column(
            "findings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rule_attribution_json",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "latency_ms", sa.Float(), nullable=False, server_default="0.0"
        ),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_eval_job_results_eval_job_id", "eval_job_results", ["eval_job_id"]
    )
    op.create_index(
        "ix_eval_job_results_case_id", "eval_job_results", ["case_id"]
    )
    op.create_index(
        "ix_eval_job_results_tenant_id", "eval_job_results", ["tenant_id"]
    )
    op.create_index(
        "ix_eval_job_results_owasp", "eval_job_results", ["owasp_category"]
    )
    op.create_index(
        "ix_results_job_passed", "eval_job_results", ["eval_job_id", "passed"]
    )
    op.create_index(
        "ix_results_tenant_created",
        "eval_job_results",
        ["tenant_id", "created_at"],
    )
    op.create_index(
        "uq_results_job_case",
        "eval_job_results",
        ["eval_job_id", "case_id"],
        unique=True,
    )

    op.create_table(
        "eval_evaluator_score_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("evaluator_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("rule_id", sa.String(80), nullable=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False, server_default="0.0"),
        sa.Column("samples", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("eval_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_eval_score_snap_tenant_id",
        "eval_evaluator_score_snapshots",
        ["tenant_id"],
    )
    op.create_index(
        "ix_eval_score_snap_evaluator_id",
        "eval_evaluator_score_snapshots",
        ["evaluator_id"],
    )
    op.create_index(
        "ix_eval_score_snap_rule_id",
        "eval_evaluator_score_snapshots",
        ["rule_id"],
    )
    op.create_index(
        "ix_eval_score_snap_snapshot_date",
        "eval_evaluator_score_snapshots",
        ["snapshot_date"],
    )
    op.create_index(
        "ix_eval_score_snap_eval_job_id",
        "eval_evaluator_score_snapshots",
        ["eval_job_id"],
    )
    op.create_index(
        "ix_snap_evaluator_rule_date",
        "eval_evaluator_score_snapshots",
        ["evaluator_id", "rule_id", "snapshot_date"],
    )
    op.create_index(
        "uq_snap_evaluator_rule_date",
        "eval_evaluator_score_snapshots",
        ["tenant_id", "evaluator_id", "rule_id", "snapshot_date"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_snap_evaluator_rule_date",
        table_name="eval_evaluator_score_snapshots",
    )
    op.drop_index(
        "ix_snap_evaluator_rule_date",
        table_name="eval_evaluator_score_snapshots",
    )
    op.drop_index(
        "ix_eval_score_snap_eval_job_id",
        table_name="eval_evaluator_score_snapshots",
    )
    op.drop_index(
        "ix_eval_score_snap_snapshot_date",
        table_name="eval_evaluator_score_snapshots",
    )
    op.drop_index(
        "ix_eval_score_snap_rule_id",
        table_name="eval_evaluator_score_snapshots",
    )
    op.drop_index(
        "ix_eval_score_snap_evaluator_id",
        table_name="eval_evaluator_score_snapshots",
    )
    op.drop_index(
        "ix_eval_score_snap_tenant_id",
        table_name="eval_evaluator_score_snapshots",
    )
    op.drop_table("eval_evaluator_score_snapshots")

    op.drop_index("uq_results_job_case", table_name="eval_job_results")
    op.drop_index("ix_results_tenant_created", table_name="eval_job_results")
    op.drop_index("ix_results_job_passed", table_name="eval_job_results")
    op.drop_index("ix_eval_job_results_owasp", table_name="eval_job_results")
    op.drop_index("ix_eval_job_results_tenant_id", table_name="eval_job_results")
    op.drop_index("ix_eval_job_results_case_id", table_name="eval_job_results")
    op.drop_index("ix_eval_job_results_eval_job_id", table_name="eval_job_results")
    op.drop_table("eval_job_results")

    op.drop_index("ix_eval_jobs_status_queued", table_name="eval_jobs")
    op.drop_index("ix_eval_jobs_tenant_queued", table_name="eval_jobs")
    op.drop_index("ix_eval_jobs_status", table_name="eval_jobs")
    op.drop_index("ix_eval_jobs_dataset_id", table_name="eval_jobs")
    op.drop_index("ix_eval_jobs_tenant_id", table_name="eval_jobs")
    op.drop_table("eval_jobs")

    op.drop_index("ix_evaluators_tenant_name", table_name="eval_evaluators")
    op.drop_index("ix_eval_evaluators_tenant_id", table_name="eval_evaluators")
    op.drop_table("eval_evaluators")

    op.drop_index(
        "ix_eval_cases_dataset_owasp", table_name="eval_dataset_cases"
    )
    op.drop_index(
        "ix_eval_cases_dataset_kind", table_name="eval_dataset_cases"
    )
    op.drop_index(
        "ix_eval_dataset_cases_owasp", table_name="eval_dataset_cases"
    )
    op.drop_index(
        "ix_eval_dataset_cases_base_id", table_name="eval_dataset_cases"
    )
    op.drop_index(
        "ix_eval_dataset_cases_tenant_id", table_name="eval_dataset_cases"
    )
    op.drop_index(
        "ix_eval_dataset_cases_dataset_id", table_name="eval_dataset_cases"
    )
    op.drop_table("eval_dataset_cases")

    op.drop_index("ix_eval_datasets_tenant_name", table_name="eval_datasets")
    op.drop_index("ix_eval_datasets_tenant_id", table_name="eval_datasets")
    op.drop_table("eval_datasets")
