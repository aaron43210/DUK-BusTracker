"""
seed.py — Seed the database with initial route and bus stops.
Run once after database creation:
    python seed.py
"""
import asyncio
from database import engine, Base, AsyncSessionLocal
from models.route import Route, BusStop
import models  # noqa: F401 — ensure all models are registered


STOPS = [
    {"order_index": 0,  "name": "Central Polytechnic",           "lat": 8.5350, "lon": 76.9908},
    {"order_index": 1,  "name": "Vattiyoorkavu Jn",              "lat": 8.5241, "lon": 76.9882},
    {"order_index": 2,  "name": "Manjadimoodu",                  "lat": 8.5233, "lon": 76.9852},
    {"order_index": 3,  "name": "Maruthankuzhi",                 "lat": 8.5138, "lon": 76.9790},
    {"order_index": 4,  "name": "Sasthamangalam",                "lat": 8.5129, "lon": 76.9710},
    {"order_index": 5,  "name": "Vellayambalam",                 "lat": 8.5116, "lon": 76.9626},
    {"order_index": 6,  "name": "Thampanoor",                    "lat": 8.4875, "lon": 76.9528},
    {"order_index": 7,  "name": "Chandrasekharan Nair Stadium",  "lat": 8.5046, "lon": 76.9512},
    {"order_index": 8,  "name": "PMG",                           "lat": 8.5084, "lon": 76.9500},
    {"order_index": 9,  "name": "Pattom",                        "lat": 8.5186, "lon": 76.9424},
    {"order_index": 10, "name": "Kesavadasapuram",               "lat": 8.5297, "lon": 76.9386},
    {"order_index": 11, "name": "Ulloor",                        "lat": 8.5299, "lon": 76.9288},
    {"order_index": 12, "name": "Pongumoodu",                    "lat": 8.5402, "lon": 76.9246},
    {"order_index": 13, "name": "Sreekaryam",                    "lat": 8.5488, "lon": 76.9172},
    {"order_index": 14, "name": "Chavadimukku",                  "lat": 8.5510, "lon": 76.9114},
    {"order_index": 15, "name": "Karyavattom",                   "lat": 8.5665, "lon": 76.8912},
    {"order_index": 16, "name": "Technopark Front",              "lat": 8.5580, "lon": 76.8770},
    {"order_index": 17, "name": "IIITMK",                        "lat": 8.5588, "lon": 76.8790},



    {"order_index": 18, "name": "Kazhakuttam",                   "lat": 8.5668, "lon": 76.8743},
    {"order_index": 19, "name": "Pallipuram",                    "lat": 8.5979, "lon": 76.8546},
    {"order_index": 20, "name": "Digital University Kerala",     "lat": 8.6158, "lon": 76.8527},
]


async def seed():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("✅ Tables created")

    async with AsyncSessionLocal() as db:
        # Check if route already exists
        from sqlalchemy import select
        existing = await db.execute(select(Route).limit(1))
        if existing.scalar_one_or_none():
            print("ℹ️  Route already seeded — skipping")
            return

        route = Route(name="Central Polytechnic ↔ Digital University Kerala",
                      description="Main DUK bus route via Vattiyoorkavu, Pattom, Technopark")
        db.add(route)
        await db.flush()   # get route.id

        for s in STOPS:
            db.add(BusStop(route_id=route.id, **s))

        await db.commit()
        print(f" Seeded route '{route.name}' with {len(STOPS)} stops")


if __name__ == "__main__":
    asyncio.run(seed())
