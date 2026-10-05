"""Global request-boundary protections for the API."""

import asyncio

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

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
