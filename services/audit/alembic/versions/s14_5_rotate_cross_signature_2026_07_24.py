"""ATF §14.5 ROTATE cross-signing — persist new-key countersignature on the
final batch of the retiring key.

Revision ID: s14_5_rotate_cross_signature_2026_07_24
Revises: s18_full_signer_fingerprint_2026_07_21
Create Date: 2026-07-24

Adds three nullable columns to ``transparency_historical_keys``:

  * ``transition_root_hash``       — root_hash of the retiring key's final
                                     TransparencyRoot at rotation time
  * ``transition_new_key_signature`` — base64 Ed25519 sig of that root's
                                     canonical signed_root_payload, minted
                                     by the NEW key
  * ``transition_new_key_fingerprint`` — the new key's fingerprint at
                                     rotation time

All three are nullable because historical rows written before this
sprint have no cross-signature. Presence together is the "cross-signed
rotation" invariant; absence means "old-style rotation, verify via
fingerprint dispatch only".

Rollback drops the columns.
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "s14_5_rotate_cross_signature_2026_07_24"
down_revision: str | None = "s18_full_signer_fingerprint_2026_07_21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "transparency_historical_keys",
        sa.Column("transition_root_hash", sa.String(64), nullable=True),
    )
    op.add_column(
        "transparency_historical_keys",
        sa.Column("transition_new_key_signature", sa.Text(), nullable=True),
    )
    op.add_column(
        "transparency_historical_keys",
        sa.Column("transition_new_key_fingerprint", sa.String(64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("transparency_historical_keys", "transition_new_key_fingerprint")
    op.drop_column("transparency_historical_keys", "transition_new_key_signature")
    op.drop_column("transparency_historical_keys", "transition_root_hash")
