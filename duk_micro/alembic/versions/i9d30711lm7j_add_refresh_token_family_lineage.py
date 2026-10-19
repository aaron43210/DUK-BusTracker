"""Add family_id + rotated to refresh_tokens for reuse detection

Revision ID: i9d30711lm7j
Revises: h8c29600kl6i
Create Date: 2026-10-01 10:00:00.000000

Adds rotation-lineage tracking so a rotated-then-replayed refresh token can be
detected and used to revoke the entire token family.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID


revision = 'i9d30711lm7j'
down_revision = 'h8c29600kl6i'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('refresh_tokens', sa.Column('family_id', UUID(as_uuid=True), nullable=True))
    op.add_column('refresh_tokens', sa.Column('rotated', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.create_index('ix_refresh_tokens_family_id', 'refresh_tokens', ['family_id'])


def downgrade():
    op.drop_index('ix_refresh_tokens_family_id', table_name='refresh_tokens')
    op.drop_column('refresh_tokens', 'rotated')
    op.drop_column('refresh_tokens', 'family_id')
