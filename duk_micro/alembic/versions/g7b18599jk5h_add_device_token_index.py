"""add index to device_token

Revision ID: g7b18599jk5h
Revises: f6a07488ij4g
Create Date: 2026-09-26 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'g7b18599jk5h'
down_revision: Union[str, None] = 'f6a07488ij4g'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(op.f('ix_users_device_token'), 'users', ['device_token'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_users_device_token'), table_name='users')
