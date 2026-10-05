"""Durable bookkeeping for background jobs (see ``JobRun``).

The API records a job when it queues it; the worker marks it finished or
failed. Bookkeeping must never fail the work it describes, so every write
here swallows its own errors and logs them.
"""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import JobRun, get_database_url
from app.utils.logger import logger

STATUS_QUEUED = "queued"
STATUS_FINISHED = "finished"
STATUS_FAILED = "failed"

JOB_RUN_RETENTION = timedelta(days=30)


def record_job_queued(
    db: AsyncSession, message_id: str, user_id: uuid.UUID, job_type: str
) -> None:
    """Add the row to the request's transaction (committed with the request)."""
    db.add(
        JobRun(
            message_id=message_id,
            user_id=user_id,
            job_type=job_type,
            status=STATUS_QUEUED,
        )
    )


async def _mark(message_id: str, status: str) -> None:
    # NullPool: each asyncio.run() is a new event loop, and pooled connections
    # do not survive one.
    engine = create_async_engine(get_database_url(), poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, class_=AsyncSession)() as session:
            await session.execute(
                update(JobRun)
                .where(JobRun.message_id == message_id)
                .values(status=status, finished_at=datetime.now(timezone.utc))
            )
            await session.commit()
    finally:
        await engine.dispose()


def mark_job_done(message_id: str | None, status: str) -> None:
    """Called by the worker, outside any event loop. Never raises."""
    if not message_id:
        return
    try:
        asyncio.run(_mark(message_id, status))
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Could not record job outcome: %s", type(exc).__name__)


async def purge_old_job_runs(db: AsyncSession) -> int:
    cutoff = datetime.now(timezone.utc) - JOB_RUN_RETENTION
    result = await db.execute(delete(JobRun).where(JobRun.created_at < cutoff))
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
