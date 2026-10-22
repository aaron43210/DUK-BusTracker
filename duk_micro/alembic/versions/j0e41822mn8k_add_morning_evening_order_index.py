"""add morning_evening_order_index

Revision ID: j0e41822mn8k
Revises: i9d30711lm7j
Create Date: 2026-10-05
"""
from alembic import op
import sqlalchemy as sa

revision = 'j0e41822mn8k'
down_revision = ('i9d30711lm7j', 'k1f52933no9l')
branch_labels = None
depends_on = None


def upgrade():
    # Use IF NOT EXISTS so this is safe to run on servers where the k1f migration
    # already added these columns (k1f was applied directly on prod but never committed).
    op.execute("ALTER TABLE bus_stops ADD COLUMN IF NOT EXISTS morning_order_index INTEGER")
    op.execute("ALTER TABLE bus_stops ADD COLUMN IF NOT EXISTS evening_order_index INTEGER")
    op.execute("UPDATE bus_stops SET morning_order_index = order_index WHERE morning_order_index IS NULL")
    op.execute("UPDATE bus_stops SET evening_order_index = order_index WHERE evening_order_index IS NULL")


def downgrade():
    op.drop_column('bus_stops', 'evening_order_index')
    op.drop_column('bus_stops', 'morning_order_index')
