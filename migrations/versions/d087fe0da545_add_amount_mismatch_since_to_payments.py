"""add amount_mismatch_since to payments

Момент первого обнаружения расхождения суммы по счёту requisites_qr — в отличие
от amount_mismatch/amount_mismatch_bank_amount (перезаписываются на каждом тике
сверки), выставляется один раз и не двигается, пока расхождение не разрешится.
Нужно для алерта Dashboard "расхождение висит дольше N часов", см.
app/services/dashboard_service.py.

Revision ID: d087fe0da545
Revises: 04786b369e8c
Create Date: 2026-08-26 00:39:35.923969

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd087fe0da545'
down_revision: Union[str, None] = '04786b369e8c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.add_column(sa.Column('amount_mismatch_since', sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('payments', schema=None) as batch_op:
        batch_op.drop_column('amount_mismatch_since')
