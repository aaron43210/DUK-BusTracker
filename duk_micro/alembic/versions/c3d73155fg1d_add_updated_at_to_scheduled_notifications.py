"""Add updated_at to scheduled_notifications

Revision ID: c3d73155fg1d
Revises: b2c62044ef0c
Create Date: 2026-09-22 14:35:00.000000

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c3d73155fg1d'
down_revision = 'b2c62044ef0c'
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column('scheduled_notifications', sa.Column('updated_at', sa.DateTime(timezone=True), nullable=True))

def downgrade() -> None:
    op.drop_column('scheduled_notifications', 'updated_at')
