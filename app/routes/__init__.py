from fastapi import APIRouter, Depends

from app.dependencies import get_current_active_user

from .auth import router as auth_router
from .health import router as health_router
from .image_to_text import router as image_to_text_router
from .jobs import router as jobs_router
from .rag_with_pdf import router as rag_with_pdf_router
from .sound_to_text import router as sound_to_text_router

router = APIRouter()

router.include_router(health_router)
router.include_router(auth_router)

# Default-deny every content route. Handler-level dependencies still provide the
# resolved user object and are dependency-cache hits, not additional auth checks.
protected_router = APIRouter(dependencies=[Depends(get_current_active_user)])
protected_router.include_router(image_to_text_router)
protected_router.include_router(jobs_router)
protected_router.include_router(rag_with_pdf_router)
protected_router.include_router(sound_to_text_router)
router.include_router(protected_router)
