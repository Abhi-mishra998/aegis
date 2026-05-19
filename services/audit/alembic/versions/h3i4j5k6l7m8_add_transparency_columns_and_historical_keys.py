"""Transparency log sprint 2026-05-15: leaf range, signing fingerprint, history.

Adds the three columns the public spec promised but the model never carried:

  - leaf_range_start_id   UUID  NULL — first audit_logs.id in the sealed window
  - leaf_range_end_id     UUID  NULL — last  audit_logs.id in the sealed window
  - signing_key_fingerprint VARCHAR(64) NULL — which root key signed this row

And introduces `transparency_historical_keys`, the lookup table the
`/transparency/keys` endpoint reads to surface rotated keys (and the table
`/receipts/verify` falls back to so historical receipts keep verifying after
a key rotation).

All new columns are nullable for back-compat: existing rows just don't know
their leaf range or signing fingerprint. New seals will populate them.

Revision ID: h3i4j5k6l7m8
Revises:    g2h3i4j5k6l7
Create Date: 2026-05-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "h3i4j5k6l7m8"
down_revision = "g2h3i4j5k6l7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── transparency_roots: new metadata columns ──────────────────────────
    op.add_column(
        "transparency_roots",
        sa.Column("leaf_range_start_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "transparency_roots",
        sa.Column("leaf_range_end_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "transparency_roots",
        sa.Column("signing_key_fingerprint", sa.String(length=64), nullable=True),
    )
    op.create_index(
        "ix_transparency_roots_signing_key_fingerprint",
        "transparency_roots",
        ["signing_key_fingerprint"],
    )

    # ── transparency_historical_keys: registry of rotated keys ────────────
    # The active key lives on disk (root-signing.pem) or in env. Once
    # rotated, the previous fingerprint + PEM lands here so old receipts
    # still verify and /transparency/keys can advertise them as historical.
    op.create_table(
        "transparency_historical_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False, unique=True),
        sa.Column("public_key_pem", sa.Text, nullable=False),
        sa.Column("algorithm", sa.String(length=32), nullable=False, server_default="ed25519"),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("retired_reason", sa.Text, nullable=True),
    )
    op.create_index(
        "ix_transparency_historical_keys_fingerprint",
        "transparency_historical_keys",
        ["fingerprint"],
    )


def downgrade() -> None:
    op.drop_index("ix_transparency_historical_keys_fingerprint", table_name="transparency_historical_keys")
    op.drop_table("transparency_historical_keys")
    op.drop_index("ix_transparency_roots_signing_key_fingerprint", table_name="transparency_roots")
    op.drop_column("transparency_roots", "signing_key_fingerprint")
    op.drop_column("transparency_roots", "leaf_range_end_id")
    op.drop_column("transparency_roots", "leaf_range_start_id")
