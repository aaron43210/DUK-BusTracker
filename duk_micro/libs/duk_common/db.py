# libs/duk_common/db.py
"""
Database engine and session factory for all services.

All services share this module to get a PostgreSQL connection.
Uses SQLAlchemy's async engine with connection pooling so we don't
open/close a new DB connection for every API request (which would be slow).

Connection pool behaviour:
  - pool_size     = 10  → up to 10 connections kept open and reused
  - max_overflow  = 20  → up to 20 extra connections during traffic spikes
  - pool_recycle  = 1800 → recycle connections every 30 minutes (prevents stale connections)
  - pool_pre_ping = True → test connection before using it (detects dropped connections)
"""
import os
from sqlalchemy.ext.asyncio import (
    create_async_engine,
    AsyncSession,
    async_sessionmaker,
    AsyncEngine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool


class Base(DeclarativeBase):
    """
    Base class for all SQLAlchemy ORM models.
    Every table model (Trip, GpsLog, BusStop, etc.) inherits from this.
    """
    pass


def create_engine(
    database_url: str | None = None,
    *,
    pool_size: int = 10,
    max_overflow: int = 20,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
    echo: bool = False,
    use_null_pool: bool = False,
) -> AsyncEngine:
    """
    Creates and returns an async SQLAlchemy database engine.

    Input:
        database_url  - PostgreSQL connection string (reads DATABASE_URL env var if None)
        pool_size     - Number of permanent connections to keep open (default: 10)
        max_overflow  - Extra connections allowed during peak load (default: 20)
        pool_timeout  - Seconds to wait for a free connection before error (default: 30)
        pool_recycle  - Recycle connections after this many seconds (default: 1800 = 30min)
        echo          - If True, logs all SQL queries (use only for debugging, never in prod)
        use_null_pool - If True, disables pooling (only for one-off batch scripts, not services)

    Output:
        AsyncEngine — the engine object used to create DB sessions

    Note:
        jit=off prevents PostgreSQL from trying to JIT-compile queries, which can cause
        issues with asyncpg and prepared statements.
    """
    url = database_url or os.environ["DATABASE_URL"]

    if use_null_pool:
        # NullPool creates a new connection per query — only for short-lived scripts
        return create_async_engine(url, echo=echo, poolclass=NullPool)

    return create_async_engine(
        url,
        echo=echo,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        pool_pre_ping=True,    # Test connection health before using it
        connect_args={
            "statement_cache_size": 0,            # Required for pgBouncer compatibility
            "prepared_statement_cache_size": 0,   # Required for pgBouncer compatibility
            "server_settings": {"jit": "off"}     # Prevents asyncpg JIT conflicts
        },
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    """
    Creates a session factory from an engine.

    Input:
        engine - The AsyncEngine returned by create_engine()

    Output:
        async_sessionmaker — call this like a function to get a DB session:
            async with session_factory() as session:
                result = await session.execute(select(Trip))

    expire_on_commit=False means objects stay usable after commit
    (important for async code where you might access attributes after await).
    """
    return async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_db_dependency(session_factory: async_sessionmaker):
    """
    Creates a FastAPI dependency that yields a DB session per request.

    Input:
        session_factory - The factory returned by create_session_factory()

    Output:
        A FastAPI dependency function — pass it to Depends() in route handlers:
            get_db = await get_db_dependency(session_factory)

            @app.get("/stops")
            async def get_stops(db: AsyncSession = Depends(get_db)):
                ...

    The session is automatically closed and rolled back on error.
    """
    async def _get_db():
        async with session_factory() as session:
            try:
                yield session
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    return _get_db
