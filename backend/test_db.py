import asyncio
from database import engine
from sqlalchemy import text

async def main():
    async with engine.begin() as conn:
        print("Checking first 3 rows...")
        res = await conn.execute(text("SELECT id, created_at, ist_time FROM gps_realtime ORDER BY id DESC LIMIT 3;"))
        for r in res.fetchall():
            print(r)

        print("\nChecking dates...")
        res = await conn.execute(text("SELECT DATE(ist_time), count(*) FROM gps_realtime GROUP BY DATE(ist_time);"))
        for r in res.fetchall():
            print(r)
            
        print("\nChecking valid_dates query...")
        res = await conn.execute(text("SELECT DISTINCT DATE(ist_time) FROM gps_realtime WHERE DATE(ist_time) BETWEEN '2026-08-09' AND '2026-08-09' AND lat IS NOT NULL ORDER BY DATE(ist_time) DESC"))
        for r in res.fetchall():
            print(r)

asyncio.run(main())
