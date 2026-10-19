import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.future import select
from libs.duk_common.models import BusStop
from libs.duk_common.settings import settings

async def clean_database_indices():
    engine = create_async_engine(settings.DATABASE_URL)
    async_session = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with async_session() as session:
        # Fetch all stops ordered by their current morning index
        result = await session.execute(
            select(BusStop).order_by(BusStop.route_id, BusStop.morning_order_index, BusStop.id)
        )
        stops = result.scalars().all()

        route_groups = {}
        for stop in stops:
            if stop.route_id not in route_groups:
                route_groups[stop.route_id] = []
            route_groups[stop.route_id].append(stop)

        for route_id, route_stops in route_groups.items():
            print(f"Fixing morning indices for route {route_id}...")
            for i, stop in enumerate(route_stops):
                stop.morning_order_index = i

        # Now do the same for evening indices
        result = await session.execute(
            select(BusStop).order_by(BusStop.route_id, BusStop.evening_order_index, BusStop.id)
        )
        stops = result.scalars().all()

        route_groups_evening = {}
        for stop in stops:
            if stop.route_id not in route_groups_evening:
                route_groups_evening[stop.route_id] = []
            route_groups_evening[stop.route_id].append(stop)

        for route_id, route_stops in route_groups_evening.items():
            print(f"Fixing evening indices for route {route_id}...")
            for i, stop in enumerate(route_stops):
                stop.evening_order_index = i

        await session.commit()
        print("Database indices cleaned and repaired successfully!")

if __name__ == "__main__":
    asyncio.run(clean_database_indices())
