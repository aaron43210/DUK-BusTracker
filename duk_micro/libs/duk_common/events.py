# libs/duk_common/events.py
"""
Event schemas and the EventBus for inter-service communication.

All services talk to each other via Redis Streams (not direct HTTP calls).
This file defines:
  - The shape (schema) of every event message
  - EventBus: a helper class to publish/consume those messages

Flow example:
  gps-ingestion  → publishes "gps.snapped"  → trip-lifecycle consumes it

Note: "trip.status" is published (admin-service, trip-lifecycle) for history/audit
but is not currently consumed by any service.
"""
from pydantic import BaseModel, Field
from datetime import datetime
from typing import Optional, List, Literal, Type
import json
import asyncio
import logging
from redis.asyncio import Redis

logger = logging.getLogger(__name__)


#  GPS Events

class GpsSnappedEvent(BaseModel):
    """
    A GPS coordinate after it has been filtered, cleaned, and snapped to the
    nearest road segment by OSRM.
    """
    device_id: str
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    speed_kmh: Optional[float] = Field(default=None, ge=0, allow_inf_nan=False)
    server_time: datetime
    should_broadcast: bool
    location_name: Optional[str] = None
    osrm_path: Optional[list] = None
    route_s: Optional[float] = None   # metres travelled along the planned route (linear reference)
    off_route: bool = False           # True when the bus left the planned corridor (OSRM-snapped)


#  Trip Events

class TripStartedEvent(BaseModel):
    """
    Fired when admin marks a trip as started.
    """
    trip_id: int = Field(gt=0)
    direction: Literal["forward", "reverse", "unscheduled", "unknown"]


class TripStatusEvent(BaseModel):
    """
    Fired when a trip's status changes.
    """
    trip_id: int = Field(gt=0)
    status: Literal["scheduled", "on_trip", "late", "completed", "cancelled"]
    direction: Literal["forward", "reverse", "unscheduled", "unknown"]


class TripStatusChangedEvent(BaseModel):
    """
    Richer version of TripStatusEvent that includes delay and cancellation info.

    `status` accepts the admin-facing values too ("active", "stop_change"), not
    just the lifecycle states, because the admin dashboard publishes these via
    this event. Omitting them previously made the event constructor raise, which
    silently dropped the realtime broadcast for a "Stop Change" action.
    """
    trip_id: int = Field(gt=0)
    status: Literal["scheduled", "on_trip", "active", "late", "stop_change", "completed", "cancelled"]
    direction: Literal["forward", "reverse", "unscheduled", "unknown"]
    late_by_minutes: Optional[int] = Field(default=None, ge=0)
    cancellation_reason: Optional[str] = None
    message: Optional[str] = None


#  Notification Events 

class ProximityAlertEvent(BaseModel):
    """
    Fired when the bus is approaching a passenger's boarding or destination stop.
    """
    trip_id: int = Field(gt=0)
    stop_id: int = Field(gt=0)
    stop_name: str = ""
    alert_type: Literal["boarding", "destination"]
    user_tokens: List[str] = Field(default_factory=list)


class EtaLateEvent(BaseModel):
    """
    Fired when ETA calculations detect the bus will be significantly late.
    """
    trip_id: int = Field(gt=0)
    delay_min: float = Field(ge=0)
    eta_min: float = Field(ge=0)
    arrival_str: str
    direction: Literal["forward", "reverse", "unscheduled", "unknown"] = "unknown"
    user_tokens: List[str] = Field(default_factory=list)


class UserPreferencesUpdatedEvent(BaseModel):
    """
    Fired when a passenger updates their stop preferences or notification settings.
    """
    user_id: str
    proximity_alert_enabled: Optional[bool] = None
    boarding_alert_stop_id: Optional[int] = Field(default=None, gt=0)
    destination_alert_stop_id: Optional[int] = Field(default=None, gt=0)
    notifications_on: Optional[bool] = None
    device_token: Optional[str] = None


#  EventBus

class EventBus:
    """
    Thin wrapper around Redis Streams for publishing and consuming events.
    """

    def __init__(self, redis: Redis):
        self.redis = redis

    # A message that cannot even be parsed/validated (a "poison" message) or that
    # keeps failing its handler must not be reclaimed forever — after this many
    # delivery attempts it is moved to a dead-letter stream and acked.
    MAX_DELIVERY_ATTEMPTS = 5

    async def _dead_letter(self, stream: str, group: str, msg_id, fields: dict, reason: str) -> None:
        """
        Move a message that cannot be processed to '<stream>.deadletter' and ack it
        on the original stream so it stops being redelivered/reclaimed. Dead-letter
        failures are logged but never raised — a broken DLQ must not wedge the loop.
        """
        try:
            raw = fields.get(b"data") if b"data" in fields else fields.get("data")
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8", errors="replace")
            await self.redis.xadd(
                f"{stream}.deadletter",
                {"data": raw if raw is not None else "", "reason": str(reason)[:500]},
                maxlen=10_000,
                approximate=True,
            )
        except Exception as e:
            logger.error("Failed to dead-letter msg %s on %s: %s", msg_id, stream, e)
        finally:
            try:
                await self.redis.xack(stream, group, msg_id)
            except Exception as e:
                logger.error("Failed to ack dead-lettered msg %s on %s: %s", msg_id, stream, e)

    async def publish(self, stream: str, event: BaseModel, maxlen: int = 10_000) -> None:
        """
        Publishes an event to a Redis Stream asynchronously.

        Input:
            stream - Stream name (e.g., "gps.snapped", "trip.status")
            event  - Any Pydantic model instance to publish
            maxlen - Stream size limit to prevent unbounded growth (default 10,000)

        The event is serialized to JSON and stored in Redis.
        """
        await self.redis.xadd(
            stream,
            {"data": event.model_dump_json()},
            maxlen=maxlen,
            approximate=True,
        )

    async def consume(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler,
        batch_size: int = 50,
        model_cls: Optional[Type[BaseModel]] = None,
    ) -> None:
        """
        Reads events from a Redis Stream in a loop and calls handler() for each one.
        """
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as e:
            msg = str(e)
            if "BUSYGROUP" not in msg:
                raise

        # PEL recovery (reclaim messages idle for > 5 minutes)
        try:
            try:
                # Try XAUTOCLAIM first (Redis 6.2+)
                _, claimed_messages = await self.redis.xautoclaim(
                    stream, group, consumer, min_idle_time=300000, start_id="0", count=50
                )
            except Exception as e:
                if "unknown command" in str(e).lower():
                    # Fallback for Redis < 6.2
                    pending_res = await self.redis.xpending_range(
                        stream, group, min="-", max="+", count=50
                    )
                    msg_ids = [
                        msg["message_id"] for msg in pending_res 
                        if msg.get("time_since_delivered", 0) >= 300000
                    ]
                    claimed_messages = []
                    if msg_ids:
                        claimed_messages = await self.redis.xclaim(
                            stream, group, consumer, min_idle_time=300000, message_ids=msg_ids
                        )
                else:
                    raise

            for msg_id, fields in claimed_messages or []:
                # Cap redelivery: a message that has already been delivered too many
                # times is poison — dead-letter it instead of reclaiming forever.
                try:
                    pending = await self.redis.xpending_range(
                        stream, group, min=msg_id, max=msg_id, count=1
                    )
                    delivered = pending[0].get("times_delivered", 0) if pending else 0
                except Exception:
                    delivered = 0
                if delivered > self.MAX_DELIVERY_ATTEMPTS:
                    await self._dead_letter(
                        stream, group, msg_id, fields,
                        reason=f"exceeded {self.MAX_DELIVERY_ATTEMPTS} delivery attempts",
                    )
                    continue
                try:
                    data = fields[b"data"] if b"data" in fields else fields.get("data")
                    if isinstance(data, bytes):
                        data = data.decode("utf-8")

                    try:
                        json_dict = json.loads(data)
                        payload = model_cls.model_validate(json_dict) if model_cls else json_dict
                    except Exception as parse_err:
                        # Unparseable/invalid: it will never succeed on retry.
                        await self._dead_letter(stream, group, msg_id, fields, reason=f"parse/validate: {parse_err}")
                        continue

                    await handler(payload)
                    await self.redis.xack(stream, group, msg_id)
                except Exception as ex:
                    logger.exception("Failed processing recovered PEL msg %s: %s", msg_id, ex)
        except Exception as e:
            logger.error("PEL recovery failed during startup: %s", e)

        # Normal loop
        while True:
            try:
                resp = await self.redis.xreadgroup(
                    group, consumer, {stream: ">"}, count=batch_size, block=5000
                )
                for _, messages in resp or []:
                    for msg_id, fields in messages:
                        try:
                            data = fields[b"data"] if b"data" in fields else fields.get("data")
                            if isinstance(data, bytes):
                                data = data.decode("utf-8")

                            try:
                                json_dict = json.loads(data)
                                payload = model_cls.model_validate(json_dict) if model_cls else json_dict
                            except Exception as parse_err:
                                # Poison message: parsing/validation can never succeed
                                # on retry, so dead-letter it immediately rather than
                                # leaving it to be reclaimed forever.
                                await self._dead_letter(stream, group, msg_id, fields, reason=f"parse/validate: {parse_err}")
                                continue

                            await handler(payload)
                            await self.redis.xack(stream, group, msg_id)
                        except Exception as e:
                            # Handler failed on a well-formed event: leave it UNACKED
                            # so it stays in the PEL and is retried (and eventually
                            # dead-lettered after MAX_DELIVERY_ATTEMPTS) by the
                            # reclaim path above.
                            logger.exception("Event handler failed for msg %s: %s", msg_id, e)
            except asyncio.CancelledError:
                logger.info("EventBus consumer loop cancelled")
                break
            except Exception as e:
                logger.error("EventBus consume error: %s", e)
                await asyncio.sleep(1)
