"""audit chain hash-version selector column

Revision ID: t4_audit_hash_version_2026_07_31
Revises: s3_split_billing_status_2026_07_21
Create Date: 2026-07-31

SEC-2026-07-31 (C6): the ``event_hash`` chain only covered six fields
(tenant_id, agent_id, action, tool, decision, request_id). ``reason``,
``metadata_json`` (findings, risk_score, PII flags, MITRE mapping,
tool arguments), and ``timestamp`` were outside the hash — silently
rewriting any of them produced a chain that still verified. Rows sealed
after this migration set ``hash_version = 2`` and the recompute mixes
in ``reason``, ``timestamp``, and ``sha256(metadata_json)`` so the
chain covers everything a compliance mapper reads.

Rollout:

  1. Column added NULLABLE with server_default = '1' so historical rows
     (which are v1) verify with the legacy scheme even after the
     migration runs but before the writer starts stamping 2.
  2. Writer switch (services/audit/writer.py) starts stamping 2 on new
     inserts as soon as the code deploys.
  3. Verifiers (services/audit/integrity.py, tools/aegis_verify/verifier.py,
     sdk/acp_client/verifier.py) already read the column via ``getattr``
     with a v1 fallback, so a rolling-restart is safe in either order.

No backfill is required — historical rows stay v1 forever, and their
signatures/roots stay verifiable via the legacy code path.
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "t4_audit_hash_version_2026_07_31"
down_revision = "113756a02ff3"  # merge head as of 2026-07-25
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column(
            "hash_version",
            sa.SmallInteger(),
            nullable=True,
            server_default="1",
            comment=(
                "compute_event_hash scheme selector; 1 = legacy 6-field, "
                "2 = v2 (+reason, +timestamp, +sha256(metadata_json)). "
                "See sdk/common/audit_hash.py."
            ),
        ),
        schema="acp_audit",
    )
    # Small NULLABLE column on a hot table — no backfill, no index.


def downgrade() -> None:
    op.drop_column("audit_logs", "hash_version", schema="acp_audit")
