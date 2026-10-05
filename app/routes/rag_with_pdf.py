"""RAG with PDF route."""

import asyncio
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import User, get_db
from app.dependencies import get_current_active_user
from app.paths import Api
from app.queues import JOB_TYPE_RAG, enqueue_rag_job
from app.schemas import JobQueuedResponse
from app.services import quota
from app.services.billing.entitlement import get_entitlement, pro_required
from app.services.job_runs import record_job_queued
from app.utils import (
    PDF_MAX_BYTES,
    delete_temp_file,
    models_supported,
    read_upload_limited,
    validate_pdf_content,
)
from app.utils.constants import CLOUD_MODELS, available_models
from app.utils.logger import logger
from app.utils.rate_limit import limiter

router = APIRouter()


@router.post(
    Api.PDF_RESPONSE,
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobQueuedResponse,
)
@limiter.limit("60/hour")
async def rag_with_pdf(
    request: Request,  # pylint: disable=unused-argument
    pdf: UploadFile | None = File(None),
    query: str = Form(..., min_length=1, max_length=2000),
    model: str = Form(..., max_length=32),
    past_request_id: Optional[str] = Form(None, max_length=64),
    current_user: User = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
) -> JobQueuedResponse:
    """Queue a PDF RAG processing job.

    Either upload a new PDF or query an existing one using past_request_id.
    Returns a job ID that can be used to check the status via GET /job/{message_id}.
    """
    # Validate model first
    if model not in models_supported:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid model."
        )

    # The plan decides which models exist for this account (see GET /me). A
    # model that only Pro may use answers 402, which opens the paywall; one
    # that is not configured at all is simply not available.
    settings = get_settings()
    entitlement = await get_entitlement(db, current_user.id)  # type: ignore[arg-type]
    if model not in available_models(settings, entitlement.pro):
        if model in available_models(settings, pro=True):
            raise pro_required(
                f"The {model} model is part of ScanGenAI Pro. Upgrade to use it."
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="That model is not available.",
        )

    # Handle optional PDF file - check if it has a valid filename
    if pdf and (not pdf.filename or not pdf.filename.strip()):
        pdf = None

    # Validate that either PDF or past_request_id is provided
    if not pdf and not past_request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Either PDF file or past_request_id must be provided.",
        )

    if pdf and past_request_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot provide both PDF and past_request_id. Use past_request_id to query an existing PDF.",
        )

    pdf_file_path: str | None = None
    try:
        job_data = {
            "query": query,
            "model": model,
            "user_id": str(current_user.id),
            "past_request_id": past_request_id,
        }

        # If PDF is provided, save it to shared volume for worker access
        if pdf:
            # Save PDF to shared volume (accessible by both web and worker containers)
            shared_pdf_dir = Path("/app/shared_files")
            shared_pdf_dir.mkdir(parents=True, exist_ok=True)

            suffix = Path(pdf.filename).suffix if pdf.filename else ".pdf"
            # Use tempfile to generate unique filename, but save to shared dir
            with tempfile.NamedTemporaryFile(
                delete=False, suffix=suffix, dir=str(shared_pdf_dir)
            ) as tmp_file:
                content = await read_upload_limited(pdf, PDF_MAX_BYTES)
                await asyncio.to_thread(validate_pdf_content, content)
                tmp_file.write(content)
                pdf_file_path = tmp_file.name

            job_data["pdf_file_path"] = pdf_file_path
            job_data["pdf_filename"] = pdf.filename or "uploaded.pdf"

        # Every question costs embeddings; a cloud model costs that provider
        # too. Both are spent together or not at all.
        kinds = [quota.KIND_PDF]
        if model in CLOUD_MODELS:
            kinds.append(quota.KIND_CLOUD_MODEL)
        await quota.consume(db, current_user.id, *kinds, pro=entitlement.pro)

        # Enqueue the job
        job_id = enqueue_rag_job(job_data)
        record_job_queued(db, job_id, current_user.id, JOB_TYPE_RAG)

        logger.info(
            "RAG job enqueued (user ID: %s) - Job ID: %s",
            current_user.id,
            job_id,
        )

        return JobQueuedResponse(
            message_id=job_id,
            status="queued",
            message="Job has been queued for processing. Poll the job endpoint with the message_id to check status.",
        )

    except HTTPException:
        delete_temp_file(pdf_file_path, silent=True)
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to enqueue RAG job: %s", exc, exc_info=True)
        delete_temp_file(pdf_file_path, silent=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue the job. Please try again later.",
        ) from exc
