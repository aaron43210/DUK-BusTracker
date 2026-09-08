# database_local.py — standard pooled session factory (drop-in replacement)
"""
Uses the shared connection-pooled engine (NOT NullPool).
NullPool is reserved for one-shot batch scripts (ml-training-job) only.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
from libs.duk_common.db import create_engine, create_session_factory

engine = create_engine(
    database_url=os.environ["DATABASE_URL"],
    pool_size=int(os.environ.get("DB_POOL_SIZE", 10)),
    max_overflow=int(os.environ.get("DB_MAX_OVERFLOW", 20)),
)
AsyncSessionLocal = create_session_factory(engine)


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
