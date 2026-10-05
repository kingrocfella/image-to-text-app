"""Main FastAPI application entry point."""

from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.lifespan import lifespan
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.security_middleware import RequestProtectionMiddleware
from app.paths import API_PREFIX, Web

# Imported for its side effect: it registers the Dramatiq actors.
from app.queues import job_queue  # noqa: F401  pylint: disable=unused-import
from app.routes import router as api_router
from app.routes import web_router
from app.routes.admin import build_admin_router
from app.utils.logger import logger

settings = get_settings()

app = FastAPI(
    title="Leon Frontier ScanGenAI API",
    lifespan=lifespan,
    # The interactive docs describe every route; served outside production only.
    docs_url=None if settings.is_production else Web.DOCS,
    redoc_url=None if settings.is_production else Web.REDOC,
    openapi_url=None if settings.is_production else Web.OPENAPI,
)

# Global protections are registered once for the entire API surface.
app.add_middleware(LoggingMiddleware)
app.add_middleware(
    RequestProtectionMiddleware,
    max_body_bytes=settings.max_request_body_bytes,
    timeout_seconds=settings.request_timeout_seconds,
    enable_hsts=settings.is_production,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_allowed_origins),
    allow_credentials=bool(settings.cors_allowed_origins),
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["Authorization", "Content-Type", "X-App-Version"],
)

app.include_router(web_router)
if settings.admin_enabled:
    # Off unless ADMIN_DASHBOARD_TOKEN is set; served at ADMIN_DASHBOARD_PATH.
    app.include_router(build_admin_router(settings))
app.include_router(api_router, prefix=API_PREFIX)
# Builds from before /v1 still call the unversioned paths. Retire them by raising
# MINIMUM_APP_VERSION above 0.0.0 (they send no version), then delete this line.
app.include_router(api_router, include_in_schema=False)


@app.exception_handler(404)
def not_found_handler(request: Request, exc: HTTPException):
    """Return a bounded first-party 404 response.

    An unknown path gets the generic body; a handler that raised 404 on
    purpose ("Job not found.") keeps its own message.
    """
    logger.warning("404 Not Found: %s %s", request.method, request.url.path)
    detail = getattr(exc, "detail", None)
    if not isinstance(detail, str) or detail == "Not Found":
        detail = "Not found"
    return JSONResponse(status_code=404, content={"detail": detail})


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
