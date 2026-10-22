"""add_user_destination_stop

Revision ID: k1f52933no9l
Revises: i9d30711lm7j
Create Date: 2026-10-06

Stub: this migration was applied on the production server but the file was
not committed to the repo. The upgrade/downgrade are no-ops because the
column it added (users.default_destination_stop_id) was subsequently removed
and replaced by the boarding_stop_id approach already in the baseline schema.
The stub exists solely so Alembic can locate the revision that production DBs
have recorded in alembic_version.
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'k1f52933no9l'
down_revision = 'i9d30711lm7j'
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
