"""add_trip_trace_and_route_geometry_cache

Revision ID: d4e85266gh2e
Revises: c3d73155fg1d
Create Date: 2026-09-23

Adds:
  - trip_trace          : per-trip GPS buffer (snapped coords), replaces gps_logs for route_history.
                          Rows are auto-deleted 1h after the trip ends by the bridge cleanup loop.
  - route_geometry_cache: pre-computed OSRM route geometry for forward/reverse directions.
                          Persists across Redis restarts. Upserted by tracking-service on startup.

These tables are purely additive — no existing table is modified or dropped.
"""
from alembic import op
import sqlalchemy as sa

revision = 'd4e85266gh2e'
down_revision = 'c3d73155fg1d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── trip_trace ──────────────────────────────────────────────────────────────
    op.create_table(
        'trip_trace',
        sa.Column('id',          sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column('trip_id',     sa.Integer(),    sa.ForeignKey('trips.id', ondelete='CASCADE'), nullable=False),
        sa.Column('raw_lat',     sa.Float(),      nullable=False),
        sa.Column('raw_lon',     sa.Float(),      nullable=False),
        sa.Column('snapped_lat', sa.Float(),      nullable=True),
        sa.Column('snapped_lon', sa.Float(),      nullable=True),
        sa.Column('speed_kmh',   sa.Float(),      nullable=True),
        sa.Column('gps_time',    sa.DateTime(timezone=True), nullable=False),
        sa.Column('recorded_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()'), nullable=False),
    )
    op.create_index('idx_trip_trace_trip_id', 'trip_trace', ['trip_id'])
    op.create_index('idx_trip_trace_gps_time', 'trip_trace', ['gps_time'])

    # ── route_geometry_cache ────────────────────────────────────────────────────
    op.create_table(
        'route_geometry_cache',
        sa.Column('id',          sa.Integer(),    primary_key=True, autoincrement=True),
        sa.Column('route_id',    sa.Integer(),    sa.ForeignKey('routes.id', ondelete='CASCADE'), nullable=False),
        sa.Column('direction',   sa.String(20),   nullable=False),
        sa.Column('coordinates', sa.JSON(),       nullable=False),
        sa.Column('computed_at', sa.DateTime(timezone=True), server_default=sa.text('NOW()')),
        sa.UniqueConstraint('route_id', 'direction', name='uq_route_geometry_cache'),
    )


def downgrade() -> None:
    op.drop_table('route_geometry_cache')
    op.drop_index('idx_trip_trace_gps_time', table_name='trip_trace')
    op.drop_index('idx_trip_trace_trip_id', table_name='trip_trace')
    op.drop_table('trip_trace')
