"""Add client_type to refresh_tokens

Revision ID: f6a07488ij4g
Revises: e5f96377hi3f
Create Date: 2026-09-25 13:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'f6a07488ij4g'
down_revision = 'e5f96377hi3f'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('refresh_tokens', sa.Column('client_type', sa.String(length=20), server_default='mobile', nullable=False))


def downgrade():
    op.drop_column('refresh_tokens', 'client_type')
