"""add refund fields to payments and manual registrations

Аннулирование уже завершённой (оплаченной/подтверждённой) покупки супер-
админом — см. DECISIONS.md, DECISIONS_LOG.md №69. Новые колонки хранят, когда,
кем и почему покупка была аннулирована постфактум (не путать с cancelled_at —
та про отмену ДО оплаты/подтверждения).

Revision ID: 49a3c9f9b68b
Revises: 2e75fb17a907
Create Date: 2026-08-04 22:01:27.029090

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '49a3c9f9b68b'
down_revision: Union[str, None] = '2e75fb17a907'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('manual_registrations', schema=None) as batch_op:
        batch_op.add_column(sa.Column('refunded_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('refund_reason', sa.String(length=2000), nullable=True))
        batch_op.add_column(sa.Column('refunded_by_panel_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_manual_registrations_refunded_by_panel_user_id_panel_users',
            'panel_users',
            ['refunded_by_panel_user_id'],
            ['id'],
        )

    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('refunded_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('refund_reason', sa.String(length=2000), nullable=True))
        batch_op.add_column(sa.Column('refunded_by_panel_user_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_payments_refunded_by_panel_user_id_panel_users',
            'panel_users',
            ['refunded_by_panel_user_id'],
            ['id'],
        )


def downgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_payments_refunded_by_panel_user_id_panel_users', type_='foreignkey'
        )
        batch_op.drop_column('refunded_by_panel_user_id')
        batch_op.drop_column('refund_reason')
        batch_op.drop_column('refunded_at')

    with op.batch_alter_table('manual_registrations', schema=None) as batch_op:
        batch_op.drop_constraint(
            'fk_manual_registrations_refunded_by_panel_user_id_panel_users', type_='foreignkey'
        )
        batch_op.drop_column('refunded_by_panel_user_id')
        batch_op.drop_column('refund_reason')
        batch_op.drop_column('refunded_at')
