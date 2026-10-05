"""PostgreSQL database connection, table creation and SQL migrations."""

from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import quote_plus

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import declarative_base

from app.config import get_settings
from app.utils.logger import logger

_settings = get_settings()
DATABASE_URL = (
    f"postgresql+asyncpg://{quote_plus(_settings.postgres_user)}"
    f":{quote_plus(_settings.postgres_password)}"
    f"@{_settings.postgres_host}:{_settings.postgres_port}/{_settings.postgres_db}"
)


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

# Serialises table creation and migrations across processes that start
# together (the API and the worker share one database).
_MIGRATION_LOCK_ID = 53_126_074


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Get database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Database session error: %s", type(exc).__name__)
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_connection() -> bool:
    """Check PostgreSQL connection."""
    try:
        async with engine.begin() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Database connection check failed: %s", type(exc).__name__)
        return False


async def init_db(target_engine=None) -> None:
    """Create missing tables, then apply every SQL migration not yet recorded.

    The model tables are created first (``CREATE TABLE IF NOT EXISTS``), so a
    fresh database starts with the current schema; the migrations in
    ``migrations/`` then bring an *existing* database up to it. Every
    migration is therefore written to be a no-op on a fresh database.
    """
    from sqlalchemy.schema import CreateTable  # pylint: disable=import-outside-toplevel

    from app.database import (  # noqa: F401  pylint: disable=unused-import,import-outside-toplevel
        postgres_models,
    )

    target = target_engine or engine
    try:
        logger.info("Initializing database tables...")
        async with target.begin() as conn:
            await conn.execute(
                text("SELECT pg_advisory_xact_lock(:lock_id)"),
                {"lock_id": _MIGRATION_LOCK_ID},
            )
            existing = set(
                await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_table_names()
                )
            )
            for table in Base.metadata.sorted_tables:
                if table.name in existing:
                    # An existing table is changed only by a migration: its
                    # model may already name columns the table does not have.
                    continue
                await conn.execute(CreateTable(table))
                for index in table.indexes:
                    await conn.run_sync(index.create)
        logger.info("Database tables initialized successfully")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to initialize database tables: %s", exc, exc_info=True)
        raise

    await run_sql_migrations(target)


def _migration_dir() -> Path:
    """Return the repository migration directory for local and container runs."""
    return Path(__file__).resolve().parents[2] / "migrations"


def _split_sql_statements(sql: str) -> list[str]:
    """Split simple migration SQL files into executable statements."""
    statements: list[str] = []
    current: list[str] = []
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        current.append(line)
        if stripped.endswith(";"):
            statement = "\n".join(current).rstrip().removesuffix(";").strip()
            if statement:
                statements.append(statement)
            current = []

    trailing = "\n".join(current).strip()
    if trailing:
        statements.append(trailing)
    return statements


async def run_sql_migrations(target_engine=None) -> None:
    """Apply SQL migrations that are not yet recorded in schema_migrations."""
    migrations_dir = _migration_dir()
    migration_files = sorted(migrations_dir.glob("*.sql"))
    if not migration_files:
        logger.warning("No SQL migrations found in %s", migrations_dir)
        return

    async with (target_engine or engine).begin() as conn:
        await conn.execute(
            text("SELECT pg_advisory_xact_lock(:lock_id)"),
            {"lock_id": _MIGRATION_LOCK_ID},
        )
        await conn.execute(text("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    filename VARCHAR(255) PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                )
                """))
        applied = set(
            (
                await conn.execute(text("SELECT filename FROM schema_migrations"))
            ).scalars()
        )
        for path in migration_files:
            if path.name in applied:
                continue
            logger.info("Applying SQL migration %s", path.name)
            for statement in _split_sql_statements(path.read_text()):
                await conn.execute(text(statement))
            await conn.execute(
                text("INSERT INTO schema_migrations (filename) VALUES (:filename)"),
                {"filename": path.name},
            )
            logger.info("Applied SQL migration %s", path.name)
