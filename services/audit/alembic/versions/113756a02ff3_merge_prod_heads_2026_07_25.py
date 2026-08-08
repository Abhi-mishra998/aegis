"""merge_prod_heads_2026_07_25

Revision ID: 113756a02ff3
Revises: a26_w4_5_composite, s14_5_rotate_cross_signature_2026_07_24
Create Date: 2026-07-25 16:19:56.031139

"""
from typing import Sequence, Union

# revision identifiers, used by Alembic.
revision: str = '113756a02ff3'
down_revision: Union[str, Sequence[str], None] = ('a26_w4_5_composite', 's14_5_rotate_cross_signature_2026_07_24')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
