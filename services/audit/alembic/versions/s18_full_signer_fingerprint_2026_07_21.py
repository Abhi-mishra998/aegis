"""backfill full-length signer fingerprints on transparency_historical_keys

Revision ID: s18_full_signer_fingerprint_2026_07_21
Revises: s3_split_billing_status_2026_07_21
Create Date: 2026-07-21

audit S18 (P2-6): ``fingerprint_public_key`` used to truncate the SHA-256
digest to the first 16 bytes (32 hex chars). New signatures use the full
64-char digest. This migration recomputes every historical row from its
stored ``public_key_pem`` so old receipts still verify — the same PEM,
just a longer fingerprint. No column type change: ``fingerprint`` is
already ``String(64)`` (which the truncated form also fit inside).

Rollback recomputes the truncated form.
"""
from collections.abc import Sequence

from alembic import op

revision: str = "s18_full_signer_fingerprint_2026_07_21"
down_revision: str | None = "s3_split_billing_status_2026_07_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Recompute the fingerprint of every historical key from its stored
    # PEM. Postgres has no built-in sha256(text), so we use pgcrypto's
    # digest() — which is included in the standard postgres distribution
    # and already enabled on the audit DB per the init migration.
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto;")
    op.execute("""
        UPDATE transparency_historical_keys
           SET fingerprint = encode(digest(public_key_pem, 'sha256'), 'hex')
    """)


def downgrade() -> None:
    # Reproduce the old truncated form (first 32 hex chars). Signatures
    # will continue to verify because verify_root_signature normalises
    # against the stored PEM, not the fingerprint.
    op.execute("""
        UPDATE transparency_historical_keys
           SET fingerprint = substring(
               encode(digest(public_key_pem, 'sha256'), 'hex') FROM 1 FOR 32
           )
    """)
