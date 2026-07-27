"""add initiating_external_user_id to payments

Revision ID: 318cef609231
Revises: ae6c278bb969
Create Date: 2026-07-27 11:54:27.251822

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '318cef609231'
down_revision: Union[str, None] = 'ae6c278bb969'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('initiating_external_user_id', sa.String(length=64), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_column('initiating_external_user_id')
