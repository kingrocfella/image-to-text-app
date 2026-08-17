"""Middleware for logging API requests."""

import time
from typing import Callable
from uuid import uuid4

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.utils.logger import logger


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all API requests and responses"""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Log request and response details."""
        start_time = time.time()

        request_id = str(uuid4())

        # Log request
        logger.info(
            "API Request: %s %s - Request ID: %s",
            request.method,
            request.url.path,
            request_id,
        )

        # Process request
        try:
            response = await call_next(request)
            process_time = time.time() - start_time

            # Log successful response
            logger.info(
                "API Response: %s %s - Status: %s - Time: %.3fs - Request ID: %s",
                request.method,
                request.url.path,
                response.status_code,
                process_time,
                request_id,
            )

            response.headers["X-Request-ID"] = request_id

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
