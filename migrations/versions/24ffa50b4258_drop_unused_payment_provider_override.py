"""drop unused payment_provider_override from platform_settings

Revision ID: 24ffa50b4258
Revises: 712b493850ae
Create Date: 2026-07-26 01:45:11.320927

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '24ffa50b4258'
down_revision: Union[str, None] = '712b493850ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Колонка осталась в схеме БД со времён удаления интернет-эквайринга
    # (TBank/VTB/Mock, DECISIONS.md №44) — поле убрано из модели PlatformSettings
    # тогда же, но соответствующая миграция на удаление колонки не была создана.
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.drop_column('payment_provider_override')


def downgrade() -> None:
    with op.batch_alter_table('platform_settings', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payment_provider_override', sa.VARCHAR(length=13), nullable=True))
