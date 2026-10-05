"""Middleware for logging API requests."""

import time
from typing import Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.paths import Web
from app.utils.logger import logger

# Docker's health check calls /health every 15 s; logging each one buried real
# traffic. A health check is logged only when it fails or is slow.
HEALTH_LOG_SLOW_SECONDS = 1.0


def should_log_request(path: str, status_code: int, seconds: float) -> bool:
    if path != Web.HEALTH:
        return True
    return status_code >= 400 or seconds >= HEALTH_LOG_SLOW_SECONDS


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all API requests and responses"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request and response details."""
        start_time = time.time()

        request_id = str(uuid4())

        # Process request
        try:
            response = await call_next(request)
            process_time = time.time() - start_time

            response.headers["X-Request-ID"] = request_id
            if not should_log_request(
                request.url.path, response.status_code, process_time
            ):
                return response

            # One line per request: method, path, status, duration. Never the
            # query string, which can carry tokens.
            logger.info(
                "API Response: %s %s - Status: %s - Time: %.3fs - Request ID: %s",
                request.method,
                request.url.path,
                response.status_code,
                process_time,
                request_id,
            )

            return response

        except Exception as exc:  # pylint: disable=broad-exception-caught
            process_time = time.time() - start_time

            # Log error
            logger.error(
                "API Error: %s %s - Time: %.3fs - Type: %s - Request ID: %s",
                request.method,
                request.url.path,
                process_time,
                type(exc).__name__,
                request_id,
                exc_info=True,
            )
            raise
