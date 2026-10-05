"""FastAPI lifespan: schema setup and the API process's background loops."""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone

from fastapi import FastAPI
from sqlalchemy import delete

from app.config import get_settings
from app.database import AsyncSessionLocal, RefreshSession, TokenBlacklist, init_db
from app.services.billing.jobs import google_billing_loop
from app.services.job_runs import purge_old_job_runs
from app.services.ops_monitor import ops_monitor_loop
from app.utils.logger import logger
from app.utils.rag_vectorstore import purge_expired_pdf_data

RETENTION_INTERVAL_SECONDS = 24 * 60 * 60


async def purge_expired_security_rows() -> int:
    """Delete blacklist entries and refresh sessions past their expiry.

    Both tables only ever answer "is this unexpired token revoked?", so a row
    for an expired token can never change an answer. Without this they grow
    for ever.
    """
    now = datetime.now(timezone.utc)
    async with AsyncSessionLocal() as session:
        blacklist = await session.execute(
            delete(TokenBlacklist).where(TokenBlacklist.expires_at < now)
        )
        sessions = await session.execute(
            delete(RefreshSession).where(RefreshSession.expires_at < now)
        )
        jobs = await purge_old_job_runs(session)
        await session.commit()
    return int(blacklist.rowcount or 0) + int(sessions.rowcount or 0) + jobs  # type: ignore[attr-defined]


async def _security_purge_loop() -> None:
    interval = get_settings().security_purge_interval_seconds
    while True:
        try:
            removed = await purge_expired_security_rows()
            if removed:
                logger.info("Purged %s expired security/job rows", removed)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error("Security purge failed: %s", type(exc).__name__, exc_info=True)
        await asyncio.sleep(interval)


async def _retention_loop() -> None:
    while True:
        try:
            async with AsyncSessionLocal() as session:
                purged = await purge_expired_pdf_data(session)
                if purged:
                    logger.info("Purged %s expired PDF vector collections", purged)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pylint: disable=broad-exception-caught
            logger.error(
                "PDF retention cleanup failed: %s", type(exc).__name__, exc_info=True
            )
        await asyncio.sleep(RETENTION_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Startup: tables + migrations, then the loops. Shutdown: stop them."""
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to initialize database: %s", exc, exc_info=True)
        raise

    # Compose runs one API process. With several uvicorn workers each would run
    # these loops; they are idempotent, but the monitor would mail twice.
    tasks = [
        asyncio.create_task(_retention_loop()),
        asyncio.create_task(_security_purge_loop()),
        asyncio.create_task(ops_monitor_loop()),
    ]
    if get_settings().billing_provider == "store":
        # Google pushes nothing; Play subscriptions are re-read hourly.
        tasks.append(asyncio.create_task(google_billing_loop()))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task
        logger.info("Application shutting down...")
