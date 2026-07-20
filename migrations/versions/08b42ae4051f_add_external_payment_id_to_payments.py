"""add external_payment_id to payments

Revision ID: 08b42ae4051f
Revises: 4cbeb5a0084a
Create Date: 2026-07-20 15:30:00.000000

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '08b42ae4051f'
down_revision: Union[str, None] = '4cbeb5a0084a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('external_payment_id', sa.String(length=100), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_column('external_payment_id')
