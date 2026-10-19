"""add_refresh_tokens_and_notif_prefs

Revision ID: e5f96377hi3f
Revises: d4e85266gh2e
Create Date: 2026-09-23

Adds:
  - refresh_tokens        : persisted refresh token hashes for the two-token auth system.
                            Indexed by user_id (FK -> users.id CASCADE).
                            Stores SHA-256 hash of the token -- never plaintext.
  - users.notify_trip_started     : bool, default TRUE
  - users.notify_delays           : bool, default TRUE
  - users.notify_admin_broadcasts : bool, default TRUE
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = 'e5f96377hi3f'
down_revision = 'd4e85266gh2e'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'refresh_tokens',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column('token_hash', sa.String(length=256), nullable=False),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('token_hash'),
    )
    op.create_index(op.f('ix_refresh_tokens_user_id'), 'refresh_tokens', ['user_id'], unique=False)

    op.add_column('users', sa.Column(
        'notify_trip_started', sa.Boolean(), nullable=False, server_default=sa.text('TRUE')
    ))
    op.add_column('users', sa.Column(
        'notify_delays', sa.Boolean(), nullable=False, server_default=sa.text('TRUE')
    ))
    op.add_column('users', sa.Column(
        'notify_admin_broadcasts', sa.Boolean(), nullable=False, server_default=sa.text('TRUE')
    ))


def downgrade() -> None:
    op.drop_column('users', 'notify_admin_broadcasts')
    op.drop_column('users', 'notify_delays')
    op.drop_column('users', 'notify_trip_started')
    op.drop_index(op.f('ix_refresh_tokens_user_id'), table_name='refresh_tokens')
    op.drop_table('refresh_tokens')
