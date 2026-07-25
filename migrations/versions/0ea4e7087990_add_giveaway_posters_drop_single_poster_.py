"""add giveaway posters, drop single poster path

Revision ID: 0ea4e7087990
Revises: 24ffa50b4258
Create Date: 2026-07-26 01:45:11.320927

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

revision: str = '0ea4e7087990'
down_revision: Union[str, None] = '24ffa50b4258'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Несколько постеров на розыгрыш вместо одного файла, загрузка через
    # веб-админку вместо ручной правки БД (DECISIONS.md №46). Данные не
    # переносятся: digital_poster_path/poster_media_cache были NULL у всех
    # существующих розыгрышей на момент миграции (загрузки не было в принципе).
    op.create_table('giveaway_posters',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('giveaway_id', sa.Integer(), nullable=False),
    sa.Column('file_path', sa.String(length=500), nullable=False),
    sa.Column('original_filename', sa.String(length=255), nullable=True),
    sa.Column('content_type', sa.String(length=100), nullable=True),
    sa.Column('media_cache', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['giveaway_id'], ['giveaways.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('giveaway_posters', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_giveaway_posters_giveaway_id'), ['giveaway_id'], unique=False)

    with op.batch_alter_table('giveaways', schema=None) as batch_op:
        batch_op.drop_column('poster_media_cache')
        batch_op.drop_column('digital_poster_path')


def downgrade() -> None:
    with op.batch_alter_table('giveaways', schema=None) as batch_op:
        batch_op.add_column(sa.Column('digital_poster_path', sa.VARCHAR(length=500), nullable=True))
        batch_op.add_column(sa.Column('poster_media_cache', sqlite.JSON(), nullable=True))

    with op.batch_alter_table('giveaway_posters', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_giveaway_posters_giveaway_id'))

    op.drop_table('giveaway_posters')
