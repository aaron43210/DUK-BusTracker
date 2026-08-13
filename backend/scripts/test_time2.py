import asyncio
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

settings = get_settings()

async def run_test():
    engine = create_async_engine(settings.DATABASE_URL)
    
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT id, server_time FROM gps_logs WHERE server_time >= '2026-07-30' ORDER BY id LIMIT 5;"))
        rows = result.fetchall()
        for row in rows:
            print(f"Row {row[0]}: {row[1]}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run_test())
