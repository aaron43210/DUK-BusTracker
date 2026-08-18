# libs/duk_common/db.py
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool
import os

class Base(DeclarativeBase):
    pass

def create_engine(database_url: str | None = None):
    url = database_url or os.environ["DATABASE_URL"]
    return create_async_engine(url, echo=False, poolclass=NullPool)

def create_session_factory(engine):
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def get_db_dependency(session_factory):
    """Returns a FastAPI dependency that yields a DB session."""
    async def _get_db():
        async with session_factory() as session:
            try:
                yield session
            finally:
                await session.close()
    return _get_db
