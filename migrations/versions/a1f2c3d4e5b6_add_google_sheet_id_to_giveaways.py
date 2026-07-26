"""add google_sheet_id to giveaways

Revision ID: a1f2c3d4e5b6
Revises: 712b493850ae
Create Date: 2026-07-26 00:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a1f2c3d4e5b6'
down_revision: Union[str, None] = '712b493850ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('giveaways', schema=None) as batch_op:
        batch_op.add_column(sa.Column('google_sheet_id', sa.String(length=200), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('giveaways', schema=None) as batch_op:
        batch_op.drop_column('google_sheet_id')
