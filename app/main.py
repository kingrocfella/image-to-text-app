"""Main FastAPI application entry point."""

import os
import asyncio
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# Import queues module to register Dramatiq actors
import app.queues.job_queue  # type: ignore  # noqa: F401
from app.database import AsyncSessionLocal, init_db
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.security_middleware import (
    DistributedRateLimitMiddleware,
    RequestProtectionMiddleware,
)
from app.routes import router as api_router
from app.utils.logger import logger
from app.utils.rag_vectorstore import purge_expired_pdf_data

load_dotenv()


APP_HOST = os.getenv("APP_HOST", "0.0.0.0")
APP_PORT = int(os.getenv("APP_PORT", "8000"))
DEBUG = os.getenv("APP_DEBUG", "False")
MAX_REQUEST_BODY_BYTES = int(os.getenv("MAX_REQUEST_BODY_BYTES", str(25 * 1024 * 1024)))
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "30"))
IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").strip().lower() in {
    "prod",
    "production",
}
if MAX_REQUEST_BODY_BYTES <= 0 or REQUEST_TIMEOUT_SECONDS <= 0:
    raise RuntimeError("Request limits must be positive")
CORS_ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv("CORS_ALLOWED_ORIGINS", "").split(",")
    if origin.strip()
]
if "*" in CORS_ALLOWED_ORIGINS:
    raise RuntimeError("CORS_ALLOWED_ORIGINS must contain explicit origins, never '*'")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Lifespan event handler for startup and shutdown events."""
    try:
        await init_db()
        logger.info("Database initialized successfully")
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to initialize database: %s", exc, exc_info=True)
        raise

    stop_retention = asyncio.Event()

    async def enforce_retention() -> None:
        while not stop_retention.is_set():
            try:
                async with AsyncSessionLocal() as session:
                    purged = await purge_expired_pdf_data(session)
                    if purged:
                        logger.info("Purged %s expired PDF vector collections", purged)
            except Exception as exc:  # pylint: disable=broad-exception-caught
                logger.error(
                    "PDF retention cleanup failed: %s",
                    type(exc).__name__,
                    exc_info=True,
                )
            try:
                await asyncio.wait_for(stop_retention.wait(), timeout=24 * 60 * 60)
            except TimeoutError:
                continue

    retention_task = asyncio.create_task(enforce_retention())
    yield
    stop_retention.set()
    await retention_task

    logger.info("Application shutting down...")


app = FastAPI(title="ScanGenAI API", lifespan=lifespan)

# Global protections are registered once for the entire API surface.
app.add_middleware(LoggingMiddleware)
app.add_middleware(DistributedRateLimitMiddleware)
app.add_middleware(
    RequestProtectionMiddleware,
    max_body_bytes=MAX_REQUEST_BODY_BYTES,
    timeout_seconds=REQUEST_TIMEOUT_SECONDS,
    enable_hsts=IS_PRODUCTION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOWED_ORIGINS,
    allow_credentials=bool(CORS_ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.exception_handler(404)
def not_found_handler(request: Request, _exc: HTTPException):
    """Return a bounded first-party 404 response."""
    logger.warning("404 Not Found: %s %s", request.method, request.url.path)
    return JSONResponse(status_code=404, content={"detail": "Not found"})


@app.exception_handler(Exception)
def global_exception_handler(request: Request, exc: Exception):
    """Handle all unhandled exceptions."""
    logger.error(
        "Unhandled exception: %s %s - %s",
        request.method,
        request.url.path,
        type(exc).__name__,
        exc_info=True,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
