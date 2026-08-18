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
from libs.duk_common.redis_client import get_redis, close_redis
from libs.duk_common.events import EventBus

from state_machine import (
    process_gps_event,
    handle_power_on,
    handle_power_off,
    auto_complete_expired_trips,
)
from database_local import AsyncSessionLocal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

AUTO_COMPLETE_INTERVAL_S = 300  # 5 minutes


# ── GPS stream consumer ────────────────────────────────────────────────────────

async def handle_gps_event(event_data: dict):
    logger.debug("[LIFECYCLE] Consuming GPS event")
    async with AsyncSessionLocal() as db:
        redis = await get_redis()
        try:
            await process_gps_event(event_data, db, redis)
        except Exception as e:
            logger.exception("[LIFECYCLE] Error processing GPS event: %s", e)


# ── POWER pub/sub listener ─────────────────────────────────────────────────────

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
            now_utc = datetime.now(timezone.utc)

            async with AsyncSessionLocal() as db:
                if event == "POWER_RESTORE":
                    await handle_power_on(db, bus, now_utc)
                elif event == "POWER_LOST":
                    await handle_power_off(db, bus, now_utc)
                else:
                    logger.debug("[LIFECYCLE] Unknown power event: %s", event)
        except Exception as exc:
            logger.exception("[LIFECYCLE] Power event handler error: %s", exc)


# ── Periodic auto-complete ─────────────────────────────────────────────────────

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


# ── Entry point ────────────────────────────────────────────────────────────────

async def main():
    redis = await get_redis()
    bus   = EventBus(redis)

    # Run auto-complete at startup
    logger.info("[LIFECYCLE] Running startup auto-complete check...")
    async with AsyncSessionLocal() as db:
        await auto_complete_expired_trips(db)

    logger.info("[LIFECYCLE] Starting trip-lifecycle consumer (GPS stream + POWER events)...")

    tasks = [
        asyncio.create_task(
            bus.consume(
                stream="gps.snapped",
                group="lifecycle-group",
                consumer="lifecycle-worker-1",
                handler=handle_gps_event,
                batch_size=10,
            )
        ),
        asyncio.create_task(power_event_listener()),
        asyncio.create_task(periodic_auto_complete()),
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

