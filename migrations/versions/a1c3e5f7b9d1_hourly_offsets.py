"""station_offsets.hourly_f: 24 PST hour-of-day offsets (JSON text)

Revision ID: a1c3e5f7b9d1
Revises: deebdc854bb7
Create Date: 2026-10-24 05:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = 'a1c3e5f7b9d1'
down_revision = 'deebdc854bb7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('station_offsets', sa.Column('hourly_f', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('station_offsets', 'hourly_f')
