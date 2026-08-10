import asyncio
import sys
import os

# Add root dir to sys.path so backend imports work
sys.path.insert(0, "/Users/aaronr/Desktop/BUS_TRACKER")

from backend.database import AsyncSessionLocal
from backend.services.trip_lifecycle import get_start_coords, get_destination_coords
from backend.models.route import BusStop
from sqlalchemy import update, select

async def test_fallbacks():
    async with AsyncSessionLocal() as db:
        try:
            print("--- Fetching Current Ends for Comparison ---")
            # Get lowest and highest index stops manually
            first_stop = (await db.execute(select(BusStop).order_by(BusStop.order_index.asc()).limit(1))).scalar_one()
            last_stop = (await db.execute(select(BusStop).order_by(BusStop.order_index.desc()).limit(1))).scalar_one()
            
            print(f"First Stop (Lowest Index) : {first_stop.name} at {first_stop.lat}, {first_stop.lon}")
            print(f"Last Stop (Highest Index) : {last_stop.name} at {last_stop.lat}, {last_stop.lon}")
            print("\n--- Clearing Roles to Force Fallback ---")
            
            # Clear all roles in the current transaction
            await db.execute(update(BusStop).values(
                is_morning_origin=False,
                is_morning_destination=False,
                is_evening_origin=False,
                is_evening_destination=False
            ))
            
            # Test Morning (forward)
            start_fwd = await get_start_coords(db, "forward")
            dest_fwd = await get_destination_coords(db, "forward")
            print(f"Morning (forward) Fallback Start : {start_fwd} -> Should match First Stop")
            print(f"Morning (forward) Fallback Dest  : {dest_fwd} -> Should match Last Stop")
            
            # Test Evening (reverse)
            start_rev = await get_start_coords(db, "reverse")
            dest_rev = await get_destination_coords(db, "reverse")
            print(f"Evening (reverse) Fallback Start : {start_rev} -> Should match Last Stop")
            print(f"Evening (reverse) Fallback Dest  : {dest_rev} -> Should match First Stop")
            
        finally:
            # Rollback to ensure no roles are actually deleted from the live DB
            await db.rollback()
            print("\n--- Transaction Rolled Back Successfully (No DB changes saved) ---")

if __name__ == "__main__":
    asyncio.run(test_fallbacks())
