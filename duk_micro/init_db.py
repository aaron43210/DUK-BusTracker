import asyncio
import os
import sys

# Append the directory so imports work
sys.path.insert(0, os.path.dirname(__file__))

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

import importlib.util
import os

def load_base(service_name):
    path = os.path.join(os.path.dirname(__file__), "services", service_name, "models_local.py")
    spec = importlib.util.spec_from_file_location(f"{service_name}_models", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.Base

# Import the most comprehensive models
AuthBase = load_base("auth-service")
AdminBase = load_base("admin-service")
NotifBase = load_base("notification-api")


async def main():
    database_url = os.environ.get("DATABASE_URL", "postgresql+asyncpg://duk:dukpassword@postgres:5432/duk_bus")
    engine = create_async_engine(database_url, echo=True)
    async with engine.begin() as conn:
        await conn.run_sync(AuthBase.metadata.create_all)
        await conn.run_sync(AdminBase.metadata.create_all)
        await conn.run_sync(NotifBase.metadata.create_all)

    print("Database initialized successfully!")

if __name__ == "__main__":
    asyncio.run(main())
