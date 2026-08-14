import asyncio
from sqlalchemy import text
from database import engine

async def migrate():
    async with engine.begin() as conn:
        print("Adding boarding_alert_stop_id...")
        try:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN boarding_alert_stop_id INTEGER;")
            )
            print("Added boarding_alert_stop_id")
        except Exception as e:
            print("Error or already exists:", e)

        print("Adding destination_alert_stop_id...")
        try:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN destination_alert_stop_id INTEGER;")
            )
            print("Added destination_alert_stop_id")
        except Exception as e:
            print("Error or already exists:", e)
            
        print("Adding last_dest_alerted_trip_id...")
        try:
            await conn.execute(
                text("ALTER TABLE users ADD COLUMN last_dest_alerted_trip_id INTEGER;")
            )
            print("Added last_dest_alerted_trip_id")
        except Exception as e:
            print("Error or already exists:", e)

    print("Migration complete.")

if __name__ == "__main__":
    asyncio.run(migrate())
