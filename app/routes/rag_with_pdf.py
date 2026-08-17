"""RAG with PDF route."""

import asyncio
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from app.database import User
from app.dependencies import get_current_active_user
from app.queues import enqueue_rag_job
from app.schemas import JobQueuedResponse
from app.utils import (
    PDF_MAX_BYTES,
    delete_temp_file,
    models_supported,
    read_upload_limited,
    validate_pdf_content,
    verify_openai_password,
)
from app.utils.logger import logger

router = APIRouter()


@router.post(
    "/pdf/get/response",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobQueuedResponse,
)
async def rag_with_pdf(
    pdf: UploadFile | None = File(None),
    query: str = Form(..., min_length=1, max_length=2000),
    model: str = Form(...),
    past_request_id: Optional[str] = Form(None),
    openai_pass: Optional[str] = Form(None),
    _current_user: User = Depends(get_current_active_user),
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

    if model == models_supported["openai"] and not verify_openai_password(openai_pass):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Incorrect OpenAI password.",
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

    try:
        job_data = {
            "query": query,
            "model": model,
            "user_id": str(_current_user.id),
            "past_request_id": past_request_id,
        }

        # If PDF is provided, save it to shared volume for worker access
        pdf_file_path: str | None = None
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

        # Enqueue the job
        job_id = enqueue_rag_job(job_data)

        logger.info(
            "RAG job enqueued (user ID: %s) - Job ID: %s",
            _current_user.id,
            job_id,
        )

        return JobQueuedResponse(
            message_id=job_id,
            status="queued",
            message="Job has been queued for processing. Use GET /job/{message_id} to check status.",
        )

    except HTTPException:
        raise
    except Exception as exc:  # pylint: disable=broad-exception-caught
        logger.error("Failed to enqueue RAG job: %s", exc, exc_info=True)
        delete_temp_file(pdf_file_path, silent=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue the job. Please try again later.",
        ) from exc
