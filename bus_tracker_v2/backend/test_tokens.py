import asyncio
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy import select
from models.user import User
import os
from dotenv import load_dotenv

load_dotenv()

async def main():
    engine = create_async_engine(os.getenv("DATABASE_URL"))
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session() as session:
        result = await session.execute(select(User))
        users = result.scalars().all()
        has_token = False
        for u in users:
            print(f"User {u.id} - {u.email}: token={u.device_token}")
            if u.device_token:
                has_token = True
        
        if not has_token:
            print("\n🚨 NO USERS HAVE A DEVICE TOKEN SAVED IN THE DATABASE 🚨")
            print("This means the frontend is failing to generate or send the token.")
        else:
            print("\n✅ Tokens found in database! The issue is likely in Firebase or backend sending.")

asyncio.run(main())
