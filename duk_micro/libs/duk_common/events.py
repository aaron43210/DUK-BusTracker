# libs/duk_common/events.py
"""
Event schemas and the EventBus for inter-service communication.

All services talk to each other via Redis Streams (not direct HTTP calls).
This file defines:
  - The shape (schema) of every event message
  - EventBus: a helper class to publish/consume those messages

Flow example:
  gps-ingestion  → publishes "gps.snapped"  → trip-lifecycle consumes it
  admin-service  → publishes "trip.status"  → notification-service consumes it
"""
from pydantic import BaseModel
from datetime import datetime
from typing import Optional, List
import json
import asyncio
import logging
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


#  GPS Events 

class GpsRawEvent(BaseModel):
    """
    A raw GPS coordinate as it arrives from the hardware device.
    Published immediately before filtering or road-snapping.

    Fields:
        device_id   - Which bus sent this (default: "bus-1")
        lat         - Latitude in decimal degrees (e.g., 8.5581)
        lon         - Longitude in decimal degrees (e.g., 76.9061)
        speed_kmh   - Speed in km/h (None if device didn't report it)
        server_time - When the server received this point (UTC)
    """
    device_id: str = "bus-1"
    lat: float
    lon: float
    speed_kmh: Optional[float] = None
    server_time: datetime


class GpsSnappedEvent(BaseModel):
    """
    A GPS coordinate after it has been filtered, cleaned, and snapped to the
    nearest road segment by OSRM.

    This is what gets stored in the database and broadcast to PWA clients.

    Fields:
        device_id        - Which bus sent this
        lat, lon         - Road-snapped coordinates (slightly adjusted from raw GPS)
        speed_kmh        - Speed in km/h
        server_time      - When the server received the original raw point (UTC)
        should_broadcast - True if this point is far enough from the last to be worth sending
        location_name    - Human-readable area name (e.g., "Pongumoodu") from reverse geocoding
    """
    device_id: str
    lat: float
    lon: float
    speed_kmh: Optional[float] = None
    server_time: datetime
    should_broadcast: bool
    location_name: Optional[str] = None


class PowerEvent(BaseModel):
    """
    Fired when the bus GPS device powers on or loses power.

    Fields:
        event       - "POWER_LOST" or "POWER_RESTORE"
        server_time - When the server received this event (UTC)
    """
    event: str
    server_time: datetime


#  Trip Events 

class TripStartedEvent(BaseModel):
    """
    Fired when admin marks a trip as started.

    Fields:
        trip_id   - Database ID of the trip row
        direction - "forward" (morning, DUK→Trivandrum) or "reverse" (evening, Trivandrum→DUK)
    """
    trip_id: int
    direction: str


class TripStatusEvent(BaseModel):
    """
    Fired when a trip's status changes (e.g., scheduled → on_trip → completed).

    Fields:
        trip_id   - Database ID of the trip row
        status    - One of: "scheduled", "on_trip", "late", "completed", "cancelled"
        direction - "forward" or "reverse"
    """
    trip_id: int
    status: str
    direction: str


class TripStatusChangedEvent(BaseModel):
    """
    Richer version of TripStatusEvent that includes delay and cancellation info.
    Used for admin-triggered updates that need to notify passengers.

    Fields:
        trip_id              - Database ID of the trip row
        status               - New status
        direction            - Trip direction
        late_by_minutes      - How many minutes late (None if on time)
        cancellation_reason  - Reason shown to passengers if cancelled
        message              - Optional admin message
    """
    trip_id: int
    status: str
    direction: str
    late_by_minutes: Optional[int] = None
    cancellation_reason: Optional[str] = None
    message: Optional[str] = None


#  Notification Events 

class ProximityAlertEvent(BaseModel):
    """
    Fired when the bus is approaching a passenger's boarding or destination stop.

    Fields:
        trip_id     - Active trip ID
        stop_id     - The stop the bus is approaching
        stop_name   - Human-readable stop name (e.g., "Kazhakuttam")
        alert_type  - "boarding" (bus approaching your pickup) or "destination" (approaching your drop-off)
        user_tokens - FCM device tokens of passengers to notify
    """
    trip_id: int
    stop_id: int
    stop_name: str = ""
    alert_type: str
    user_tokens: List[str]


class EtaLateEvent(BaseModel):
    """
    Fired when ETA calculations detect the bus will be significantly late.

    Fields:
        trip_id     - Active trip ID
        delay_min   - How many minutes behind schedule
        eta_min     - Estimated minutes until arrival at target stop
        arrival_str - Human-readable arrival time (e.g., "07:42 PM")
        direction   - Trip direction
        user_tokens - FCM device tokens of affected passengers
    """
    trip_id: int
    delay_min: float
    eta_min: float
    arrival_str: str
    direction: str = ""
    user_tokens: List[str] = []


class ScheduledNotificationDueEvent(BaseModel):
    """
    Fired by the notification dispatcher when a pre-scheduled reminder is due.
    Example: "Your bus departs in 30 minutes" sent automatically.

    Fields:
        notif_id - Database ID of the scheduled notification
        trip_id  - Related trip ID
        title    - Push notification title
        body     - Push notification body text
    """
    notif_id: int
    trip_id: int
    title: str
    body: str


class UserPreferencesUpdatedEvent(BaseModel):
    """
    Fired when a passenger updates their stop preferences or notification settings.
    trip-lifecycle service listens for this to refresh its in-memory cache.

    Fields:
        user_id                   - Passenger's user ID
        proximity_alert_enabled   - Whether to send proximity alerts
        boarding_alert_stop_id    - Stop to watch for boarding alerts
        destination_alert_stop_id - Stop to watch for destination alerts
        notifications_on          - Master switch for all notifications
        device_token              - FCM device token for push notifications
    """
    user_id: str
    proximity_alert_enabled: Optional[bool] = None
    boarding_alert_stop_id: Optional[int] = None
    destination_alert_stop_id: Optional[int] = None
    notifications_on: Optional[bool] = None
    device_token: Optional[str] = None


class SendOtpEvent(BaseModel):
    """
    Fired when a user requests an OTP to sign in.
    """
    email: str
    name: str
    otp: str


#  EventBus 

class EventBus:
    """
    Thin wrapper around Redis Streams for publishing and consuming events.

    Redis Streams (XADD/XREADGROUP) are like a message queue:
    - Multiple consumers can read from the same stream
    - Each message is acknowledged individually, so nothing is lost on crash
    - Messages are automatically trimmed after 10,000 entries

    Usage:
        bus = EventBus(redis)
        await bus.publish("gps.snapped", my_event)
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    async def publish(self, stream: str, event: BaseModel) -> None:
        """
        Publishes an event to a Redis Stream.

        Input:
            stream - Stream name (e.g., "gps.snapped", "trip.status")
            event  - Any Pydantic model instance to publish

        Output: nothing (fire and forget)

        The event is serialized to JSON and stored in Redis.
        Stream is capped at 10,000 messages to prevent unbounded growth.
        """
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
    ) -> None:
        """
        Reads events from a Redis Stream in a loop and calls handler() for each one.

        Input:
            stream     - Stream name to read from
            group      - Consumer group name (allows multiple services to share a stream)
            consumer   - Unique name for this consumer within the group
            handler    - async function(dict) → called for each event
            batch_size - Max events to read in a single call (default: 50)

        Output: runs forever until the asyncio task is cancelled

        Each successfully handled message is acknowledged so it won't be
        re-delivered. If handler() raises, the error is logged but the loop continues.
        """
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception:
            pass  # Group already exists — that's fine

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
