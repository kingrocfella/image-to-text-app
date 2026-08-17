"""Sound to text conversion routes."""

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, status

from app.database import User
from app.dependencies.dependencies import get_current_active_user
from app.schemas import JobQueuedResponse
from app.queues import enqueue_sound_job
from app.utils import (
    AUDIO_MAX_BYTES,
    delete_temp_file,
    read_upload_limited,
    validate_sound_content,
)
from app.utils.logger import logger
from app.utils.utils import validate_sound_file

router = APIRouter()

# Shared directory for audio files (same as PDFs)
SHARED_AUDIO_DIR = Path("/app/shared_files")


@router.post(
    "/convert/sound/text",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobQueuedResponse,
)
async def transcribe_sound_to_text(
    file: UploadFile = File(...),
    _current_user: User = Depends(get_current_active_user),
) -> JobQueuedResponse:
    """Queue a sound-to-text conversion job.

    Returns a job ID that can be used to check the status via GET /job/{message_id}.
    """
    logger.info("Sound-to-text request (user ID: %s)", _current_user.id)

    # Validate sound file
    if not validate_sound_file(file):
        logger.warning("Invalid sound upload metadata (user ID: %s)", _current_user.id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid sound file.",
        )

    audio_file_path: str | None = None
    try:
        # Save audio to shared volume for worker access
        SHARED_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

        suffix = Path(file.filename).suffix if file.filename else ".wav"
        with tempfile.NamedTemporaryFile(
            delete=False, suffix=suffix, dir=str(SHARED_AUDIO_DIR)
        ) as tmp_file:
            content = await read_upload_limited(file, AUDIO_MAX_BYTES)
            validate_sound_content(content, file.filename)
            tmp_file.write(content)
            audio_file_path = tmp_file.name

        # Enqueue the job
        job_data = {
            "audio_file_path": audio_file_path,
            "filename": file.filename or "audio.wav",
            "user_id": str(_current_user.id),
        }
        job_id = enqueue_sound_job(job_data)

        logger.info(
            "Sound-to-text job enqueued (user ID: %s) - Job ID: %s",
            _current_user.id,
            job_id,
        )

        return JobQueuedResponse(
            message_id=job_id,
            status="queued",
            message="Job has been queued for processing. Use GET /job/{message_id} to check status.",
        )

    except HTTPException:
        delete_temp_file(audio_file_path, silent=True)
        raise
    except Exception as exc:
        logger.error("Failed to enqueue sound-to-text job: %s", exc, exc_info=True)
        delete_temp_file(audio_file_path, silent=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to enqueue the job. Please try again later.",
        ) from exc
