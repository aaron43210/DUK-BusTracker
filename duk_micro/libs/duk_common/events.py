# libs/duk_common/events.py
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List
import json
from redis.asyncio import Redis


# ── GPS Events ────────────────────────────────────────────────────────────────

class GpsRawEvent(BaseModel):
    device_id: str = "bus-1"
    lat: float
    lon: float
    speed_kmh: Optional[float] = None
    server_time: datetime


class GpsSnappedEvent(BaseModel):
    device_id: str
    lat: float
    lon: float
    speed_kmh: Optional[float] = None
    server_time: datetime
    should_broadcast: bool


class PowerEvent(BaseModel):
    event: str          # "POWER_LOST" | "POWER_RESTORE"
    server_time: datetime


# ── Trip Events ───────────────────────────────────────────────────────────────

class TripStartedEvent(BaseModel):
    trip_id: int
    direction: str      # "forward" | "reverse"


class TripStatusEvent(BaseModel):
    trip_id: int
    status: str         # "scheduled" | "on_trip" | "late" | "completed" | "cancelled"
    direction: str


class TripStatusChangedEvent(BaseModel):
    """Rich status change with full monolith fields for admin-triggered updates."""
    trip_id: int
    status: str
    direction: str
    late_by_minutes: Optional[int] = None
    cancellation_reason: Optional[str] = None
    message: Optional[str] = None


# ── Notification Events ───────────────────────────────────────────────────────

class ProximityAlertEvent(BaseModel):
    trip_id: int
    stop_id: int
    stop_name: str = ""
    alert_type: str             # "boarding" | "destination"
    user_tokens: List[str]


class EtaLateEvent(BaseModel):
    trip_id: int
    delay_min: float
    eta_min: float
    arrival_str: str
    direction: str = ""
    user_tokens: List[str] = []


class ScheduledNotificationDueEvent(BaseModel):
    """Fired by the scheduled-dispatcher when a pre-cancel reminder is due."""
    notif_id: int
    trip_id: int
    title: str
    body: str


class UserPreferencesUpdatedEvent(BaseModel):
    """Published by auth-service after PATCH /preferences so trip-lifecycle can refresh its cache."""
    user_id: str
    proximity_alert_enabled: Optional[bool] = None
    boarding_alert_stop_id: Optional[int] = None
    destination_alert_stop_id: Optional[int] = None
    notifications_on: Optional[bool] = None
    device_token: Optional[str] = None


# ── EventBus (Redis Streams) ──────────────────────────────────────────────────

class EventBus:
    def __init__(self, redis: Redis):
        self.redis = redis

    async def publish(self, stream: str, event: BaseModel):
        await self.redis.xadd(
            stream,
            {"data": event.model_dump_json()},
            maxlen=10_000,
            approximate=True,
        )

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler,
        batch_size: int = 50,
    ):
        import asyncio
        import logging
        logger = logging.getLogger(__name__)
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # group already exists

        while True:
            try:
                resp = await self.redis.xreadgroup(
                    group, consumer, {stream: ">"}, count=batch_size, block=5000
                )
                for _, messages in resp or []:
                    for msg_id, fields in messages:
                        try:
                            await handler(json.loads(fields["data"]))
                            await self.redis.xack(stream, group, msg_id)
                        except Exception as e:
                            logger.exception(
                                "Event handler failed for msg %s: %s", msg_id, e
                            )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("EventBus consume error: %s", e)
                await asyncio.sleep(1)

