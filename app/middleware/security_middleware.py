"""Global request-boundary protections for the API."""

import asyncio
import hashlib
import os
import time
from collections.abc import Awaitable, Callable
from typing import Any

import redis
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.database.redis import get_redis_url
from app.utils.logger import logger


class RequestProtectionMiddleware:
    """Bound request bodies and duration, then attach API security headers."""

    def __init__(
        self,
        app: ASGIApp,
        max_body_bytes: int,
        timeout_seconds: float,
        enable_hsts: bool,
    ) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.timeout_seconds = timeout_seconds
        self.enable_hsts = enable_hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        body_parts: list[bytes] = []
        total = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            total += len(body)
            if total > self.max_body_bytes:
                await JSONResponse(
                    {"detail": "Request body too large"}, status_code=413
                )(scope, receive, send)
                return
            body_parts.append(body)
            if not message.get("more_body", False):
                break

        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                # StreamingResponse asks receive() again to monitor disconnects.
                # Returning an endless empty request spins that task at 100% CPU.
                return await receive()
            replayed = True
            return {
                "type": "http.request",
                "body": b"".join(body_parts),
                "more_body": False,
            }

        response_started = False

        async def protected_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
                headers = list(message.get("headers", []))
                existing = {name.lower() for name, _value in headers}
                defaults = [
                    (b"cache-control", b"no-store"),
                    (
                        b"content-security-policy",
                        b"default-src 'none'; frame-ancestors 'none'",
                    ),
                    (
                        b"permissions-policy",
                        b"camera=(), microphone=(), geolocation=()",
                    ),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff"),
                    (b"x-frame-options", b"DENY"),
                ]
                headers.extend(
                    (name, value) for name, value in defaults if name not in existing
                )
                if self.enable_hsts and b"strict-transport-security" not in existing:
                    headers.append(
                        (
                            b"strict-transport-security",
                            b"max-age=31536000; includeSubDomains",
                        )
                    )
                message["headers"] = headers
            await send(message)

        try:
            await asyncio.wait_for(
                self.app(scope, replay_receive, protected_send),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            logger.warning("Request deadline exceeded: %s", scope.get("path", ""))
            if not response_started:
                await JSONResponse({"detail": "Request timed out"}, status_code=504)(
                    scope, replay_receive, protected_send
                )


class DistributedRateLimitMiddleware:
    """Redis-backed fixed-window limits for abuse-sensitive endpoints."""

    DEFAULT_LIMITS = {
        ("POST", "/auth/register"): (5, 3600),
        ("POST", "/auth/login"): (10, 900),
        ("POST", "/auth/refresh"): (30, 300),
        ("POST", "/convert/image/text"): (60, 3600),
        ("POST", "/convert/sound/text"): (30, 3600),
        ("POST", "/pdf/get/response"): (30, 3600),
    }

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.environment = os.getenv("ENVIRONMENT", "development").strip().lower()
        self.fail_closed = self.environment in {"prod", "production"}
        self._unavailable_until = 0.0
        self.client = redis.from_url(
            get_redis_url(),
            socket_connect_timeout=0.25,
            socket_timeout=0.25,
            decode_responses=True,
        )

    def _increment(self, key: str, window_seconds: int) -> int:
        return int(
            self.client.eval(
                """
                local count = redis.call('INCR', KEYS[1])
                if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
                return count
                """,
                1,
                key,
                window_seconds,
            )
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit_config = self.DEFAULT_LIMITS.get((scope["method"], scope["path"]))
        if limit_config is None:
            await self.app(scope, receive, send)
            return

        limit, window_seconds = limit_config
        client_host = (scope.get("client") or ("unknown", 0))[0]
        identity = hashlib.sha256(str(client_host).encode("utf-8")).hexdigest()[:24]
        bucket = int(time.time()) // window_seconds
        key = f"rate:v1:{scope['method']}:{scope['path']}:{identity}:{bucket}"

        if time.monotonic() < self._unavailable_until and not self.fail_closed:
            await self.app(scope, receive, send)
            return

        try:
            count = await asyncio.to_thread(self._increment, key, window_seconds + 1)
        except redis.RedisError as exc:
            self._unavailable_until = time.monotonic() + 30
            logger.error("Rate-limit store unavailable: %s", type(exc).__name__)
            if self.fail_closed:
                await JSONResponse(
                    {"detail": "Service temporarily unavailable"}, status_code=503
                )(scope, receive, send)
                return
            await self.app(scope, receive, send)
            return

        if count > limit:
            await JSONResponse(
                {"detail": "Too many requests"},
                status_code=429,
                headers={"Retry-After": str(window_seconds)},
            )(scope, receive, send)
            return

        await self.app(scope, receive, send)
