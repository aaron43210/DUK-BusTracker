"""Baseline (pre-Alembic schema)

Revision ID: a1b51933fe9b
Revises:
Create Date: 2026-09-22 13:00:00.000000

The original baseline file was never committed, which left b2c62044ef0c pointing
at a missing parent and made `alembic upgrade head` fail on every database.
This restores the root of the chain as a no-op: databases that existed before
Alembic already have these tables, and fresh databases are created at head by
sql_scripts/init_schema.sql (stamped at the current head).
"""

# revision identifiers, used by Alembic.
revision = 'a1b51933fe9b'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
