"""add gift promo fields

Акция «за каждые N купленных за раз — M в подарок» (DECISIONS_LOG.md №87): условия
акции на коллекции, положенные/невыданные подарки на покупках, признак подарочного номера.

Revision ID: bb5666e9afa0
Revises: 8828f80abfd6
Create Date: 2026-10-30 21:34:13.608186

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'bb5666e9afa0'
down_revision: Union[str, None] = '8828f80abfd6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('giveaways', schema=None) as batch_op:
        batch_op.add_column(sa.Column('promo_buy_n', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('promo_gift_m', sa.Integer(), nullable=True))

    with op.batch_alter_table('manual_registrations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gift_quantity', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('gifts_not_issued', sa.Integer(), server_default='0', nullable=False))

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('gift_quantity', sa.Integer(), server_default='0', nullable=False))
        batch_op.add_column(sa.Column('gifts_not_issued', sa.Integer(), server_default='0', nullable=False))

    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_gift', sa.Boolean(), server_default=sa.text('0'), nullable=False))



def downgrade() -> None:
    with op.batch_alter_table('tickets', schema=None) as batch_op:
        batch_op.drop_column('is_gift')

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_column('gifts_not_issued')
        batch_op.drop_column('gift_quantity')

    with op.batch_alter_table('manual_registrations', schema=None) as batch_op:
        batch_op.drop_column('gifts_not_issued')
        batch_op.drop_column('gift_quantity')

    with op.batch_alter_table('giveaways', schema=None) as batch_op:
        batch_op.drop_column('promo_gift_m')
        batch_op.drop_column('promo_buy_n')

