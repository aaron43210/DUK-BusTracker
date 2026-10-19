"""add morning_evening_order_index

Revision ID: j0e41822mn8k
Revises: i9d30711lm7j
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = 'j0e41822mn8k'
down_revision = 'i9d30711lm7j'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('bus_stops', sa.Column('morning_order_index', sa.Integer(), nullable=True))
    op.add_column('bus_stops', sa.Column('evening_order_index', sa.Integer(), nullable=True))
    # Backfill from existing order_index — zero data loss
    op.execute("UPDATE bus_stops SET morning_order_index = order_index, evening_order_index = order_index")


def downgrade():
    op.drop_column('bus_stops', 'evening_order_index')
    op.drop_column('bus_stops', 'morning_order_index')
