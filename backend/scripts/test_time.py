import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from datetime import timezone
from zoneinfo import ZoneInfo

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import get_settings

settings = get_settings()

async def run_test():
    engine = create_async_engine(settings.DATABASE_URL)
    ist = ZoneInfo("Asia/Kolkata")
    
    async with engine.connect() as conn:
        result = await conn.execute(text("SELECT id, server_time FROM gps_logs WHERE server_time >= '2026-07-30 00:00:00' ORDER BY id LIMIT 5;"))
        rows = result.fetchall()
        for row in rows:
            print(f"Row {row[0]}:")
            print(f"  Raw: {row[1]} (tzinfo: {row[1].tzinfo})")
            
            if row[1].tzinfo is None:
                dt_utc = row[1].replace(tzinfo=timezone.utc)
                ist_time = dt_utc.astimezone(ist)
            else:
                ist_time = row[1].astimezone(ist)
                
            print(f"  Formatted IST: {ist_time.strftime('%I:%M %p')}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run_test())
