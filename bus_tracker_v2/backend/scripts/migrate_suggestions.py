import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from config import get_settings

settings = get_settings()

async def run_migration():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        print("Truncating suggestions table...")
        await conn.execute(text("TRUNCATE TABLE suggestions RESTART IDENTITY CASCADE;"))
        
    async with engine.begin() as conn:
        print("Adding columns to suggestions table...")
        try:
            await conn.execute(text("ALTER TABLE suggestions ADD COLUMN user_id UUID REFERENCES users(id) ON DELETE SET NULL;"))
            await conn.execute(text("ALTER TABLE suggestions ADD COLUMN status VARCHAR(30) DEFAULT 'pending';"))
            await conn.execute(text("ALTER TABLE suggestions ADD COLUMN admin_response TEXT;"))
            print("Successfully altered suggestions schema.")
        except Exception as e:
            print(f"Error altering suggestions table: {e}")
            
    async with engine.begin() as conn:
        print("Adding eta_notif_sent to trips table...")
        try:
            await conn.execute(text("ALTER TABLE trips ADD COLUMN eta_notif_sent BOOLEAN DEFAULT FALSE;"))
            print("Successfully altered trips schema.")
        except Exception as e:
            print(f"Error altering trips table: {e}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(run_migration())
