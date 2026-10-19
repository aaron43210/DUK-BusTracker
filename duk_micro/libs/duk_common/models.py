# services/admin-service/models_local.py
import uuid
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, Float, Date, JSON, BigInteger, Text, ForeignKey, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass


class Trip(Base):
    __tablename__ = "trips"
    id                  = Column(Integer, primary_key=True)
    route_id            = Column(Integer, ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    bus_id              = Column(Integer, nullable=True)
    date                = Column(Date, nullable=True)
    direction           = Column(String, nullable=False)
    status              = Column(String, nullable=False, default="scheduled")
    started_at          = Column(DateTime(timezone=True), nullable=True)
    ended_at            = Column(DateTime(timezone=True), nullable=True)
    late_by_minutes     = Column(Integer, nullable=True)
    cancellation_reason = Column(String, nullable=True)
    eta_notif_sent      = Column(Boolean, default=False)
    visited_stops       = Column(JSON, nullable=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())
    updated_at          = Column(DateTime(timezone=True), onupdate=func.now())


class BusStop(Base):
    __tablename__ = "bus_stops"
    id                    = Column(Integer, primary_key=True)
    route_id              = Column(Integer, ForeignKey("routes.id", ondelete="CASCADE"))
    name                  = Column(String)
    lat                   = Column(Float)
    lon                   = Column(Float)
    order_index           = Column(Integer)               # kept for backward compat / fallback
    morning_order_index   = Column(Integer, nullable=True)
    evening_order_index   = Column(Integer, nullable=True)
    morning_time          = Column(String, nullable=True)
    evening_time          = Column(String, nullable=True)
    is_morning_origin      = Column(Boolean, default=False)
    is_morning_destination = Column(Boolean, default=False)
    is_evening_origin      = Column(Boolean, default=False)
    is_evening_destination = Column(Boolean, default=False)
    created_at             = Column(DateTime(timezone=True), server_default=func.now())


class Route(Base):
    __tablename__ = "routes"
    id          = Column(Integer, primary_key=True)
    name        = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())


class GpsLog(Base):
    __tablename__ = "gps_logs"
    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    lat         = Column(Float, nullable=True)
    lon         = Column(Float, nullable=True)
    speed       = Column(Float, nullable=True)
    event       = Column(String(30), nullable=True)
    ist_time    = Column(DateTime(timezone=False), nullable=True)
    server_time = Column("created_at", DateTime(timezone=True), server_default=func.now())
    trip_id     = Column(Integer, nullable=True)
    gps_time    = Column(DateTime(timezone=True), nullable=True)
    source      = Column(String(20), nullable=True, index=True)


class GpsRealtime(Base):
    __tablename__ = "gps_realtime"
    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    lat         = Column(Float, nullable=True)
    lon         = Column(Float, nullable=True)
    speed       = Column(Float, nullable=True)
    event       = Column(String(30), nullable=True)
    ist_time    = Column(DateTime(timezone=False), nullable=True)
    server_time = Column("created_at", DateTime(timezone=True), server_default=func.now())
    trip_id     = Column(Integer, nullable=True, unique=True)
    gps_time    = Column(DateTime(timezone=True), nullable=True)
    source      = Column(String(20), nullable=True, index=True)


class User(Base):
    __tablename__ = "users"
    id               = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email            = Column(String(120), unique=True)
    name             = Column(String(120), nullable=True)
    boarding_stop_id = Column(Integer, ForeignKey("bus_stops.id"), nullable=True)
    verified         = Column(Boolean, default=False)
    device_token     = Column(String, nullable=True, index=True)
    notifications_on = Column(Boolean, default=True)
    notify_trip_started      = Column(Boolean, default=True, nullable=False)
    notify_delays            = Column(Boolean, default=True, nullable=False)
    notify_admin_broadcasts  = Column(Boolean, default=True, nullable=False)
    proximity_alert_enabled = Column(Boolean, default=False)
    proximity_alert_type = Column(String, nullable=True)
    proximity_alert_value = Column(Float, nullable=True)
    proximity_alert_for = Column(String, nullable=True)
    last_alerted_trip_id = Column(Integer, nullable=True)
    boarding_alert_stop_id = Column(Integer, nullable=True)
    destination_alert_stop_id = Column(Integer, nullable=True)
    last_dest_alerted_trip_id = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    last_seen = Column(DateTime(timezone=True), nullable=True)


class AdminBroadcast(Base):
    __tablename__ = "admin_broadcasts"
    id         = Column(Integer, primary_key=True, index=True)
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    target     = Column(String(30), default="all")
    sent_at    = Column(DateTime(timezone=True), server_default=func.now())
    sent_count = Column(Integer, default=0)
    success    = Column(Boolean, default=True)


class Suggestion(Base):
    __tablename__ = "suggestions"
    id             = Column(Integer, primary_key=True, index=True)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    trip           = Column(String(50), nullable=True)
    location       = Column(String(100), nullable=True)
    suggestion     = Column(Text, nullable=False)
    user_id        = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    status         = Column(String(30), default="pending")
    admin_response = Column(Text, nullable=True)


class ScheduledNotification(Base):
    __tablename__ = "scheduled_notifications"
    id        = Column(Integer, primary_key=True, index=True)
    trip_id   = Column(Integer, nullable=True)
    send_at   = Column(DateTime(timezone=True), nullable=False)
    title     = Column(String(200), nullable=False)
    body      = Column(Text, nullable=False)
    status    = Column(String(30), default="pending")
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class InAppNotification(Base):
    __tablename__ = "in_app_notifications"
    id         = Column(Integer, primary_key=True, index=True)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    title      = Column(String(200), nullable=False)
    body       = Column(Text, nullable=False)
    type       = Column(String(50), nullable=False)
    is_read    = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"
    id = Column(Integer, primary_key=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    ist_time = Column(DateTime(timezone=False), nullable=True)
    admin_username = Column(String(100), nullable=False)
    action = Column(String(100), nullable=False)
    endpoint = Column(String(255), nullable=True)
    method = Column(String(10), nullable=True)
    ip_address = Column(String(64), nullable=True)
    device_os = Column(String(100), nullable=True)
    browser = Column(String(100), nullable=True)
    user_agent = Column(Text, nullable=True)
    location_info = Column(JSON, nullable=True)
    changes = Column(JSON, nullable=True)
    status_code = Column(Integer, nullable=True)
    success = Column(Boolean, default=True)


class UserNotificationPreference(Base):
    __tablename__ = "user_notification_preferences"
    id = Column(Integer, primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    route_id = Column(Integer, ForeignKey("routes.id", ondelete="CASCADE"))
    boarding_stop_id = Column(Integer, ForeignKey("bus_stops.id"))
    destination_stop_id = Column(Integer, ForeignKey("bus_stops.id"))
    direction = Column(String)
    is_active = Column(Boolean)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class TripTrace(Base):
    """
    Per-trip GPS buffer — stores every snapped GPS point for the CURRENT active trip.
    Queried by the PWA to draw the full blue trail from trip start, even if the user
    opens the app mid-trip. Rows are automatically deleted 1 hour after the trip ends.
    Unscheduled trips: nothing is written here (no trip_id).
    """
    __tablename__ = "trip_trace"
    id          = Column(BigInteger, primary_key=True, autoincrement=True)
    trip_id     = Column(Integer, ForeignKey("trips.id", ondelete="CASCADE"), nullable=False, index=True)
    raw_lat     = Column(Float, nullable=False)   # filtered GPS coordinate
    raw_lon     = Column(Float, nullable=False)
    snapped_lat = Column(Float, nullable=True)    # math-snapped or OSRM-snapped coordinate
    snapped_lon = Column(Float, nullable=True)
    speed_kmh   = Column(Float, nullable=True)
    osrm_path   = Column(JSON, nullable=True)     # [[lon,lat],...] path from previous point
    gps_time    = Column(DateTime(timezone=True), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class RouteGeometryCache(Base):
    """
    Pre-computed OSRM route geometry for forward and reverse trips.
    Computed once at service startup (or when stops change) and persisted here.
    Redis caches this for 24h; if Redis restarts, this DB row repopulates it instantly.
    Frontend uses this as the grey planned route on the map — zero per-request OSRM calls.
    """
    __tablename__ = "route_geometry_cache"
    id          = Column(Integer, primary_key=True)
    route_id    = Column(Integer, ForeignKey("routes.id", ondelete="CASCADE"), nullable=False)
    direction   = Column(String(20), nullable=False)   # 'forward' | 'reverse'
    coordinates = Column(JSON, nullable=False)          # [[lon, lat], ...] (flattened MultiLineString)
    computed_at = Column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (UniqueConstraint("route_id", "direction", name="uq_route_geometry_cache"),)


class RefreshToken(Base):
    """
    Persisted refresh tokens for the two-token authentication system.
    - access_token:  15-minute JWT, used for every API request.
    - refresh_token: 1-year opaque token stored here as a SHA-256 hash.
    When the access_token expires, the PWA calls POST /api/v1/auth/refresh
    with the plaintext refresh_token. We hash it, look it up here, and
    issue a new access_token. Logout deletes all rows for the user.
    """
    __tablename__ = "refresh_tokens"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    token_hash = Column(String(256), unique=True, nullable=False)  # SHA-256 hash — never store plaintext
    client_type = Column(String(20), nullable=False, server_default="mobile")  # "mobile" or "web"
    # Rotation lineage for refresh-token reuse detection. All tokens descending
    # from one login share a family_id; `rotated` is set once a token has been
    # exchanged for a successor. Re-presenting a rotated token after its grace
    # window signals theft/replay and revokes the whole family.
    family_id  = Column(UUID(as_uuid=True), nullable=True, index=True)
    rotated    = Column(Boolean, nullable=False, server_default="false")
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
