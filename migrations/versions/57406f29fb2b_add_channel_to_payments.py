"""add channel to payments

Revision ID: 57406f29fb2b
Revises: 0d83124fd1e4
Create Date: 2026-07-22 06:44:55.654808

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '57406f29fb2b'
down_revision: Union[str, None] = '0d83124fd1e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                'channel',
                sa.Enum('TELEGRAM', 'VK', 'MAX', name='channeltype', native_enum=False),
                nullable=True,
            )
        )


def downgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_column('channel')
