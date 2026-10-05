"""Liveness and readiness.

``/health`` answers without touching a dependency: it is what Docker's health
check and `make rotate-secrets` wait on. ``/ready`` checks PostgreSQL and
Redis, for an operator asking "can this actually serve a request?".
"""

import asyncio

import redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.database import check_connection
from app.paths import Web

router = APIRouter(include_in_schema=False)


@router.get(Web.HEALTH, status_code=200)
def health_check():
    """Health check endpoint to verify the API server is running."""
    return JSONResponse(status_code=200, content={"status": "ok"})


def _redis_ok() -> bool:
    try:
        client = redis.from_url(
            get_settings().redis_url, socket_connect_timeout=1, socket_timeout=1
        )
        try:
            return bool(client.ping())
        finally:
            client.close()
    except redis.RedisError:
        return False


@router.get(Web.READY)
async def readiness_check():
    """200 only when the database and the job queue both answer."""
    database_ok, queue_ok = await asyncio.gather(
        check_connection(), asyncio.to_thread(_redis_ok)
    )
    ready = database_ok and queue_ok
    return JSONResponse(
        status_code=200 if ready else 503,
        content={
            "status": "ok" if ready else "unavailable",
            "database": "ok" if database_ok else "unavailable",
            "queue": "ok" if queue_ok else "unavailable",
        },
    )
