"""Real-PostgreSQL support for unittest-style tests (the billing rules).

``TEST_DATABASE_URL`` names a throwaway database: ``fresh_schema`` drops and
rebuilds it exactly as startup does (model tables, then every migration).
"""

import unittest

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from app.database.postgres import init_db
from tests.conftest import TEST_DATABASE_URL

requires_postgres = unittest.skipUnless(
    TEST_DATABASE_URL, "TEST_DATABASE_URL is not set"
)


async def fresh_schema() -> AsyncEngine:
    engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await init_db(engine)
    return engine
