"""Add prev_root_hash to transparency_roots (Merkle-of-Merkles chain).

Crypto Sprint 2026-05-15: each daily root now commits to the immediately
previous (tenant, root_date) row's root_hash. Together with the existing
ed25519 signature on the root payload, this turns transparency_roots into
an append-only chain — any silent rewrite of yesterday's root is
mathematically detectable to anyone holding an older root.

The column is nullable for back-compat with existing rows (the first ever
root per tenant naturally has no predecessor).

Revision ID: g2h3i4j5k6l7
Revises:    f1a2b3c4d5e6
Create Date: 2026-05-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "g2h3i4j5k6l7"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "transparency_roots",
        sa.Column("prev_root_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transparency_roots", "prev_root_hash")
