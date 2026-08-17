"""Unified job status routes."""

from fastapi import APIRouter, Depends, HTTPException, status

from app.database import User
from app.dependencies.dependencies import get_current_active_user
from app.queues import (
    JOB_TYPE_IMAGE,
    JOB_TYPE_RAG,
    JOB_TYPE_SOUND,
    JobNotFoundError,
    get_job_status,
)
from app.schemas import (
    ImageJobResult,
    JobStatusFailed,
    JobStatusPending,
    ResponseItem,
    SoundJobResult,
)
from app.utils.logger import logger

router = APIRouter()


@router.get(
    "/job/{message_id}",
    status_code=status.HTTP_200_OK,
    response_model=ResponseItem
    | SoundJobResult
    | ImageJobResult
    | JobStatusPending
    | JobStatusFailed,
)
def get_job_status_endpoint(
    message_id: str,
    current_user: User = Depends(get_current_active_user),
) -> (
    ResponseItem | SoundJobResult | ImageJobResult | JobStatusPending | JobStatusFailed
):
    """Get the status of any background job.

    Returns the job status and result if completed.
    """
    try:
        status_info = get_job_status(message_id, str(current_user.id))
        job_type = status_info.get("job_type")

        # If job is finished, return appropriate result type
        if status_info["status"] == "finished" and status_info.get("result"):
            result = status_info["result"]

            # Return type based on job type
            if job_type == JOB_TYPE_SOUND:
                return SoundJobResult(
                    content=result.get("content", ""),
                    filename=result.get("filename"),
                )
            elif job_type == JOB_TYPE_RAG:
                return ResponseItem(
                    content=result.get("content", ""),
                    request_id=result.get("request_id"),
                )
            elif job_type == JOB_TYPE_IMAGE:
                return ImageJobResult(
                    content=result.get("content", ""),
                    filename=result.get("filename"),
                    segments_count=result.get("segments_count"),
                )
            else:
                raise ValueError(f"Unknown job type: {job_type}")

        # If job is pending
        if status_info["status"] == "pending":
            return JobStatusPending(
                message_id=message_id,
                status="pending",
                message=status_info.get("message", "Job is being processed"),
            )

        # If job failed or unknown status
        job_status = status_info["status"]
        if job_status not in ("failed", "unknown"):
            job_status = "unknown"
        return JobStatusFailed(
            message_id=message_id,
            status=job_status,  # type: ignore[arg-type]
            error=status_info.get("error", "Unknown error"),
        )

    except JobNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found.",
        ) from exc
    except Exception as exc:
        logger.error("Failed to get job status: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to retrieve job status.",
        ) from exc
