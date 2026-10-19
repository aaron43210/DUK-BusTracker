"""Add status to scheduled_notifications

Revision ID: b2c62044ef0c
Revises: a1b51933fe9b
Create Date: 2026-09-22 13:10:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'b2c62044ef0c'
down_revision = 'a1b51933fe9b'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('scheduled_notifications', sa.Column('status', sa.String(length=30), nullable=True))

def downgrade() -> None:
    op.drop_column('scheduled_notifications', 'status')
