import asyncio
import os
import sys

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import AsyncSessionLocal
from models.user import User
from sqlalchemy import select

async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(User.id, User.email, User.verified, User.notifications_on, User.device_token))
        users = result.fetchall()
        print(f"Total users: {len(users)}")
        for u in users:
            print(f"ID: {u.id}, Email: {u.email}, Verified: {u.verified}, NotifOn: {u.notifications_on}, HasToken: {bool(u.device_token)}")

if __name__ == "__main__":
    asyncio.run(main())
