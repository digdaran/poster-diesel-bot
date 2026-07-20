"""add cancelled_at to payments

Revision ID: 3cc3f35458a0
Revises: 08b42ae4051f
Create Date: 2026-07-20 18:00:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '3cc3f35458a0'
down_revision: Union[str, None] = '08b42ae4051f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('cancelled_at', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_column('cancelled_at')
