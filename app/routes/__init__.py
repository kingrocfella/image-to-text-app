"""Route aggregation.

``router`` is the JSON API: mounted under ``/v1`` by ``app/main.py`` and, for
builds from before ``/v1``, at its old unversioned paths. ``web_router`` holds
what browsers and email links open; it is never versioned.
"""

from fastapi import APIRouter, Depends

from app.dependencies import get_current_active_user
from app.dependencies.app_version import require_supported_app_version

from .auth import router as auth_router
from .auth import web_router as auth_web_router
from .billing import router as billing_router
from .billing import webhook_router as billing_webhook_router
from .client_logs import router as client_logs_router
from .health import router as health_router
from .image_to_text import router as image_to_text_router
from .jobs import router as jobs_router
from .me import router as me_router
from .rag_with_pdf import router as rag_with_pdf_router
from .sound_to_text import router as sound_to_text_router

# Every API call, sign-in included, passes the app-version gate.
router = APIRouter(dependencies=[Depends(require_supported_app_version)])
router.include_router(auth_router)

# Default-deny every content route. Handler-level dependencies still provide the
# resolved user object and are dependency-cache hits, not additional auth checks.
protected_router = APIRouter(dependencies=[Depends(get_current_active_user)])
protected_router.include_router(me_router)
protected_router.include_router(image_to_text_router)
protected_router.include_router(jobs_router)
protected_router.include_router(rag_with_pdf_router)
protected_router.include_router(sound_to_text_router)
protected_router.include_router(client_logs_router)
protected_router.include_router(billing_router)
router.include_router(protected_router)

web_router = APIRouter()
web_router.include_router(health_router)
web_router.include_router(auth_web_router)
web_router.include_router(billing_webhook_router)
