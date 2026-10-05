"""PostgreSQL-backed fixed-window rate limiting, shared by every API worker.

Routes opt in with ``@limiter.limit("5/minute")`` beneath the route decorator.
Each hit atomically upserts one counter row per (route, client) in
``rate_limit_buckets``. The client is the signed-in user when the endpoint
takes ``current_user``, otherwise the client IP. Keys are SHA-256 hashed, so
the table never holds an IP address or user ID.

The limiter fails closed: if the counter cannot be updated the request is
refused with 503 rather than allowed through unlimited.
"""

import functools
import hashlib
import inspect
import re
import time
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar, cast

from fastapi import HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import get_settings
from app.database.postgres import AsyncSessionLocal
from app.utils.logger import logger

Endpoint = TypeVar("Endpoint", bound=Callable[..., Awaitable[Any]])

_PERIOD_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
_SPEC_RE = re.compile(r"^\s*(\d+)\s*/\s*(second|minute|hour|day)\s*$")
_CLEANUP_INTERVAL_SECONDS = 60.0

_UPSERT = text("""
    INSERT INTO rate_limit_buckets (key, count, reset_at, updated_at)
    VALUES (:key, 1, now() + make_interval(secs => :window), now())
    ON CONFLICT (key) DO UPDATE SET
        count = CASE
            WHEN rate_limit_buckets.reset_at <= now() THEN 1
            ELSE rate_limit_buckets.count + 1
        END,
        reset_at = CASE
            WHEN rate_limit_buckets.reset_at <= now()
                THEN now() + make_interval(secs => :window)
            ELSE rate_limit_buckets.reset_at
        END,
        updated_at = now()
    RETURNING count AS hits, CEIL(EXTRACT(EPOCH FROM (reset_at - now())))::int AS retry_after
    """)
_CLEANUP = text(
    "DELETE FROM rate_limit_buckets WHERE reset_at < now() - interval '1 day'"
)


def parse_limit(spec: str) -> tuple[int, int]:
    """Parse ``"5/minute"`` into ``(5, 60)``."""
    match = _SPEC_RE.match(spec)
    if not match or int(match.group(1)) < 1:
        raise ValueError(f"Invalid rate limit {spec!r}; expected e.g. '5/minute'")
    return int(match.group(1)), _PERIOD_SECONDS[match.group(2)]


def get_rate_limit_key(request: Request) -> str:
    """Client IP for rate limits.

    When ``TRUST_PROXY_HEADERS`` is true, uses the **last** address in
    ``X-Forwarded-For``: the one the reverse proxy in front of the API added.
    Earlier entries are whatever the client sent and can be forged, so the
    first one must never be trusted. This is right whether the proxy
    overwrites the header or appends to it, as long as exactly one proxy sits
    in front (the API port only listens on 127.0.0.1, so nothing else can
    reach it). Behind a second proxy layer such as a CDN, the last entry would
    be that layer's address instead; see docs/security.md.
    """
    if get_settings().trust_proxy_headers:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            client_ip = forwarded.split(",")[-1].strip()
            if client_ip:
                return client_ip
    return request.client.host if request.client else "unknown"


def _client_identity(request: Request, kwargs: dict[str, Any]) -> str:
    user_id = getattr(kwargs.get("current_user"), "id", None)
    if user_id is not None:
        return f"user:{user_id}"
    return f"ip:{get_rate_limit_key(request)}"


class RateLimiter:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal
    ) -> None:
        self._session_factory = session_factory
        self._next_cleanup_at = 0.0

    async def hit(self, scope: str, identity: str, max_hits: int, window: int) -> None:
        """Count one request; raise 429 over the limit, 503 if uncountable."""
        key = hashlib.sha256(f"{scope}:{identity}".encode()).hexdigest()
        try:
            async with self._session_factory() as session:
                if time.monotonic() >= self._next_cleanup_at:
                    self._next_cleanup_at = time.monotonic() + _CLEANUP_INTERVAL_SECONDS
                    await session.execute(_CLEANUP)
                row = (
                    await session.execute(
                        _UPSERT, {"key": key, "window": float(window)}
                    )
                ).one()
                await session.commit()
        except Exception:
            logger.error("Rate limiter unavailable scope=%s", scope, exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Please retry shortly.",
                headers={"Retry-After": "5"},
            ) from None

        if row.hits > max_hits:
            retry_after = max(1, int(row.retry_after))
            logger.warning(
                "Rate limit exceeded scope=%s count=%d max=%d retry_after=%ds",
                scope,
                row.hits,
                max_hits,
                retry_after,
            )
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests. Please try again later.",
                headers={"Retry-After": str(retry_after)},
            )

    def limit(self, spec: str) -> Callable[[Endpoint], Endpoint]:
        """Decorate an async endpoint that takes ``request: Request``."""
        max_hits, window = parse_limit(spec)

        def decorator(endpoint: Endpoint) -> Endpoint:
            if not inspect.iscoroutinefunction(endpoint):
                raise TypeError(f"{endpoint.__qualname__} must be async to rate limit")
            if "request" not in inspect.signature(endpoint).parameters:
                raise TypeError(f"{endpoint.__qualname__} needs a `request` parameter")
            scope = f"{endpoint.__module__}.{endpoint.__qualname__}"

            @functools.wraps(endpoint)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                request = kwargs["request"]
                identity = _client_identity(request, kwargs)
                await self.hit(scope, identity, max_hits, window)
                return await endpoint(*args, **kwargs)

            return cast(Endpoint, wrapper)

        return decorator


limiter = RateLimiter()
