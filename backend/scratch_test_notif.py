import asyncio
from database import async_session
from sqlalchemy import select
from models.notification import Suggestion
from models.user import User

async def main():
    async with async_session() as db:
        res = await db.execute(
            select(Suggestion, User)
            .outerjoin(User, Suggestion.user_id == User.id)
            .order_by(Suggestion.id.desc())
            .limit(1)
        )
        row = res.first()
        if not row:
            print("No suggestions found.")
            return
        s, user = row
        print(f"Latest Suggestion ID: {s.id}")
        print(f"Suggestion: {s.suggestion}")
        print(f"Admin Response: {s.admin_response}")
        if user:
            print(f"User Email: {user.email}")
            print(f"User Device Token: {user.device_token}")
            print(f"User Notifications On: {user.notifications_on}")
        else:
            print("No associated user found (user is None).")

if __name__ == "__main__":
    asyncio.run(main())
