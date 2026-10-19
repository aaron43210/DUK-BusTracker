# services/trip-lifecycle/consumer.py
"""
Trip lifecycle consumer — entry point for the trip-lifecycle microservice.

Responsibilities:
  1. Consume 'gps.snapped' Redis stream -> process_gps_event
  2. Listen to 'bus:power' Redis pub/sub -> handle_power_on / handle_power_off
  3. Run auto_complete_expired_trips at startup and every 5 minutes
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
# Default bus id when a power event's payload doesn't carry one (single-bus deploy).
# Payloads may override via data["bus_id"] for multi-bus deployments.
BUS_ID = int(os.environ.get("BUS_ID", "1"))
from libs.duk_common.redis_client import get_redis, close_redis
from libs.duk_common.events import EventBus, GpsSnappedEvent

from state_machine import (
    process_gps_event,
    handle_power_on,
    handle_power_off,
    auto_complete_expired_trips,
    COMPLETION_TIMERS,
    _complete_trip_after_grace,
)
from database_local import AsyncSessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUTO_COMPLETE_INTERVAL_S = 300  # 5 minutes


#  GPS stream consumer 

async def handle_gps_event(event: GpsSnappedEvent):
    """Consumes GPS events from the stream and processes them.
    Args:
        event (GpsSnappedEvent): The validated GPS event.
    """
    logger.debug("[LIFECYCLE] Consuming GPS event")
    async with AsyncSessionLocal() as db:
        redis = await get_redis()
        try:
            # Extract bus_id from the event dynamically
            bus_id = int(event.device_id) if event.device_id.isdigit() else 1
            await process_gps_event(event.model_dump(), db, redis, bus_id)
        except Exception as e:
            logger.exception("[LIFECYCLE] Error processing GPS event: %s", e)


#  POWER pub/sub listener 

async def power_event_listener():
    """Listens to the 'bus:power' Redis pub/sub channel for POWER_ON/POWER_LOST."""
    redis  = await get_redis()
    bus    = EventBus(redis)
    pubsub = redis.pubsub()
    await pubsub.subscribe("bus:power")
    logger.info("[LIFECYCLE] Subscribed to 'bus:power' pub/sub channel")

    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            data    = json.loads(message["data"])
            event   = data.get("event", "")
            # Allow payload to override the env-var bus_id (multi-bus deployments)
            bus_id  = int(data.get("bus_id", BUS_ID))
            now_utc = datetime.now(timezone.utc)

            async with AsyncSessionLocal() as db:
                if event == "POWER_RESTORE":
                    await handle_power_on(db, bus, now_utc, bus_id)
                elif event == "POWER_LOST":
                    await handle_power_off(db, bus, now_utc, bus_id)
                else:
                    logger.debug("[LIFECYCLE] Unknown power event: %s", event)
        except Exception as exc:
            logger.exception("[LIFECYCLE] Power event handler error: %s", exc)


#  Periodic auto-complete 

async def periodic_auto_complete():
    """Every AUTO_COMPLETE_INTERVAL_S seconds, expire time-window-exceeded trips."""
    while True:
        await asyncio.sleep(AUTO_COMPLETE_INTERVAL_S)
        try:
            async with AsyncSessionLocal() as db:
                modified = await auto_complete_expired_trips(db)
                if modified:
                    logger.info("[LIFECYCLE] Periodic auto-complete: expired trips marked done")
        except Exception as exc:
            logger.error("[LIFECYCLE] Periodic auto-complete error: %s", exc)


#  Startup Timer Recovery 

async def recover_grace_timers(bus: EventBus):
    """Resume any interrupted grace timers from Redis on startup."""
    try:
        redis = await get_redis()
        # Use SCAN instead of KEYS to avoid blocking Redis on startup.
        keys = []
        cursor = 0
        while True:
            cursor, batch = await redis.scan(cursor, match="grace_timer:*", count=100)
            keys.extend(batch)
            if cursor == 0:
                break
        for key in keys:
            try:
                # The shared client uses decode_responses=True, so keys are str
                # already (calling .decode() raised and lost every timer).
                if isinstance(key, bytes):
                    key = key.decode("utf-8")
                trip_id = int(key.split(":")[1])
                ttl = await redis.ttl(key)
                if ttl > 0:
                    task = asyncio.create_task(_complete_trip_after_grace(trip_id, bus, delay=ttl))
                    COMPLETION_TIMERS[trip_id] = task
                    logger.info("[LIFECYCLE] Recovered grace timer for trip #%d (remaining TTL: %ds)", trip_id, ttl)
            except Exception as e:
                logger.error("[LIFECYCLE] Failed to recover timer for key %s: %s", key, e)
    except Exception as e:
        logger.error("[LIFECYCLE] Failed to fetch grace timer keys: %s", e)


#  Heartbeat Loop for K8s exec liveness probe 
HEARTBEAT_FILE = "/tmp/heartbeat"

async def heartbeat_loop():
    """Update a file every 15s so K8s exec probes know the event loop is alive."""
    while True:
        try:
            with open(HEARTBEAT_FILE, "w") as f:
                f.write(str(datetime.now(timezone.utc).timestamp()))
        except Exception as e:
            logger.error("[LIFECYCLE] Failed to write heartbeat: %s", e)
        await asyncio.sleep(15)


#  Entry point 

async def main():
    redis = await get_redis()
    bus   = EventBus(redis)

    # Run auto-complete at startup
    logger.info("[LIFECYCLE] Running startup auto-complete check...")
    async with AsyncSessionLocal() as db:
        await auto_complete_expired_trips(db)

    # Recover interrupted grace timers
    await recover_grace_timers(bus)

    logger.info("[LIFECYCLE] Starting trip-lifecycle consumer (GPS stream + POWER events)...")

    tasks = [
        asyncio.create_task(
            bus.consume(
                stream="gps.snapped",
                group="lifecycle-group",
                consumer="lifecycle-worker-1",
                handler=handle_gps_event,
                batch_size=10,
                model_cls=GpsSnappedEvent,
            )
        ),
        asyncio.create_task(power_event_listener()),
        asyncio.create_task(periodic_auto_complete()),
        asyncio.create_task(heartbeat_loop()),
    ]

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        for t in tasks:
            t.cancel()
    finally:
        await close_redis()


if __name__ == "__main__":
    asyncio.run(main())

