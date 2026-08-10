"""
migration_add_stop_times.py
Run once from the project root: python3 migration_add_stop_times.py

Adds morning_time / evening_time columns to bus_stops and pre-populates
all existing stops from the original hardcoded schedule.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "backend"))

from dotenv import load_dotenv
load_dotenv("backend/.env")

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

DATABASE_URL = os.environ["DATABASE_URL"]

MORNING_SCHEDULE = {
    'Central Polytechnic':          '07:30 AM',
    'Vattiyoorkavu Jn':             '07:35 AM',
    'Manjadimoodu':                 '07:38 AM',
    'Maruthankuzhi':                '07:42 AM',
    'Sasthamangalam':               '07:47 AM',
    'Vellayambalam':                '07:52 AM',
    'Thampanoor':                   '08:00 AM',
    'Chandrasekharan Nair Stadium': '08:08 AM',
    'PMG':                          '08:12 AM',
    'Pattom':                       '08:16 AM',
    'Kesavadasapuram':              '08:22 AM',
    'Ulloor':                       '08:27 AM',
    'Pongumoodu':                   '08:32 AM',
    'Sreekaryam':                   '08:37 AM',
    'Chavadimukku':                 '08:42 AM',
    'Karyavattom':                  '08:48 AM',
    'IIITMK':                       '08:52 AM',
    'Technopark Front':             '08:56 AM',
    'Kazhakuttam':                  '09:02 AM',
    'Pallipuram':                   '09:12 AM',
    'Digital University Kerala':    '09:20 AM',
}

EVENING_SCHEDULE = {
    'Digital University Kerala':    '05:40 PM',
    'Pallipuram':                   '05:48 PM',
    'Kazhakuttam':                  '05:58 PM',
    'Technopark Front':             '06:04 PM',
    'IIITMK':                       '06:08 PM',
    'Karyavattom':                  '06:12 PM',
    'Chavadimukku':                 '06:18 PM',
    'Sreekaryam':                   '06:23 PM',
    'Pongumoodu':                   '06:28 PM',
    'Ulloor':                       '06:33 PM',
    'Kesavadasapuram':              '06:38 PM',
    'Pattom':                       '06:44 PM',
    'PMG':                          '06:48 PM',
    'Chandrasekharan Nair Stadium': '06:52 PM',
    'Thampanoor':                   '07:00 PM',
    'Vellayambalam':                '07:08 PM',
    'Sasthamangalam':               '07:13 PM',
    'Maruthankuzhi':                '07:18 PM',
    'Manjadimoodu':                 '07:22 PM',
    'Vattiyoorkavu Jn':             '07:25 PM',
    'Central Polytechnic':          '07:30 PM',
}


async def run():
    engine = create_async_engine(DATABASE_URL, echo=False)
    Session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        for col in ("morning_time", "evening_time"):
            try:
                await db.execute(text(f"ALTER TABLE bus_stops ADD COLUMN IF NOT EXISTS {col} VARCHAR(10);"))
                print(f"  + Column ready: {col}")
            except Exception as e:
                print(f"  - Skipped {col}: {e}")

        result = await db.execute(text("SELECT id, name FROM bus_stops;"))
        stops = result.fetchall()
        print(f"\n  Found {len(stops)} stops. Populating times…\n")

        updated, skipped = 0, 0
        for stop_id, name in stops:
            morning = MORNING_SCHEDULE.get(name)
            evening = EVENING_SCHEDULE.get(name)
            if morning or evening:
                await db.execute(
                    text("UPDATE bus_stops SET morning_time=:m, evening_time=:e WHERE id=:id"),
                    {"m": morning, "e": evening, "id": stop_id}
                )
                print(f"  + {name:40s} → {morning} / {evening}")
                updated += 1
            else:
                print(f"  ? {name:40s} → no match (NULL)")
                skipped += 1

        await db.commit()
        print(f"\n  ✓ Done. Updated={updated}, Left NULL={skipped}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(run())
