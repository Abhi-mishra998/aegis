"""Merge two parallel heads (audit_chain_sequence + audit_log_append_only_trigger).

Both forked from y0a1b2c3d4e5; this migration is the join point so
``alembic upgrade head`` resolves to a single revision again.

Revision ID: aa_merge_2026_06_20
Revises: z1a2b3c4d5e6, 3a519b48a6f2
"""
from __future__ import annotations


revision: str = "aa_merge_2026_06_20"
down_revision: tuple[str, str] = ("z1a2b3c4d5e6", "3a519b48a6f2")
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
