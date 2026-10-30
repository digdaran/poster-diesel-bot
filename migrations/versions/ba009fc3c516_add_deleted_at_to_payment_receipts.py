"""add deleted_at to payment receipts

Пометка «файл чека удалён с диска» (удаление чеков архивной коллекции, DECISIONS_LOG.md №89).

Revision ID: ba009fc3c516
Revises: bb5666e9afa0
Create Date: 2026-10-30 22:17:25.782222

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'ba009fc3c516'
down_revision: Union[str, None] = 'bb5666e9afa0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('payment_receipts', schema=None) as batch_op:
        batch_op.add_column(sa.Column('deleted_at', sa.DateTime(), nullable=True))



def downgrade() -> None:
    with op.batch_alter_table('payment_receipts', schema=None) as batch_op:
        batch_op.drop_column('deleted_at')

