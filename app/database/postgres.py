"""PostgreSQL database connection and configuration."""

import os
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.utils.logger import logger

load_dotenv()

# PostgreSQL connection settings
POSTGRES_USER = os.getenv("POSTGRES_USER")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD")
POSTGRES_DB = os.getenv("POSTGRES_DB")
POSTGRES_HOST = os.getenv("POSTGRES_HOST")
POSTGRES_PORT = os.getenv("POSTGRES_PORT")

DATABASE_URL = f"postgresql+asyncpg://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"


def get_database_url() -> str:
    """Get the PostgreSQL database URL."""
    return DATABASE_URL


engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=10,
    max_overflow=20,
)
AsyncSessionLocal = async_sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False
)

# Base class for models
Base = declarative_base()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Database session error: %s", exc, exc_info=True)
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_connection() -> bool:
    """Check PostgreSQL connection."""
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        logger.debug("Database connection check successful")
        return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Database connection check failed: %s", exc, exc_info=True)
        return False


async def init_db():
    """Initialize database tables."""
    try:
        logger.info("Initializing database tables...")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables initialized successfully")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to initialize database tables: %s", exc, exc_info=True)
        raise
