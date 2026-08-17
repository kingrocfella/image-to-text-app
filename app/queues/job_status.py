"""Owner-authorized job status utilities for background results."""

import json
from typing import Any, Dict

from dramatiq.results.errors import ResultFailure, ResultMissing, ResultTimeout

from app.queues.job_queue import (
    JOB_METADATA_KEY_PREFIX,
    JOB_TYPE_IMAGE,
    JOB_TYPE_RAG,
    JOB_TYPE_SOUND,
    _get_redis_client,
    process_image_job,
    process_rag_job,
    process_sound_job,
    result_backend,
)
from app.utils.logger import logger


class JobNotFoundError(Exception):
    """Raised when a job is absent or does not belong to the caller."""


def _get_job_metadata(message_id: str) -> dict[str, str] | None:
    """Get and validate owner-bound job metadata from Redis."""
    client = _get_redis_client()
    key = f"{JOB_METADATA_KEY_PREFIX}{message_id}"
    metadata_value = client.get(key)
    if isinstance(metadata_value, bytes):
        metadata_value = metadata_value.decode("utf-8")
    if not isinstance(metadata_value, str):
        return None
    try:
        metadata = json.loads(metadata_value)
    except json.JSONDecodeError:
        logger.error("Invalid job metadata for message_id: %s", message_id)
        return None
    if not isinstance(metadata, dict):
        return None
    job_type = metadata.get("job_type")
    owner_user_id = metadata.get("owner_user_id")
    if not isinstance(job_type, str) or not isinstance(owner_user_id, str):
        return None
    return {"job_type": job_type, "owner_user_id": owner_user_id}


def get_job_status(message_id: str, requesting_user_id: str) -> Dict[str, Any]:
    """Get the status of any job by message ID.

    Automatically determines the job type and queries the appropriate actor.
    """
    try:
        logger.info("Checking job status for message_id: %s", message_id)

        metadata = _get_job_metadata(message_id)
        if metadata is None or metadata["owner_user_id"] != requesting_user_id:
            raise JobNotFoundError("Job not found")

        job_type = metadata["job_type"]
        logger.info("Job type for message_id %s: %s", message_id, job_type)

        # Select the appropriate actor based on job type
        if job_type == JOB_TYPE_SOUND:
            actor = process_sound_job
        elif job_type == JOB_TYPE_RAG:
            actor = process_rag_job
        elif job_type == JOB_TYPE_IMAGE:
            actor = process_image_job
        else:
            raise ValueError(f"Unknown job type: {job_type}")

        result = _try_get_result(actor, message_id)
        result["job_type"] = job_type
        return result

    except JobNotFoundError:
        raise
    except Exception as e:
        logger.error("Error fetching job status: %s", e, exc_info=True)
        return {
            "message_id": message_id,
            "status": "unknown",
            "error": str(e),
        }


def _try_get_result(actor, message_id: str) -> Dict[str, Any]:
    """Try to get result from a specific actor."""
    try:
        logger.info("Trying to get result for message_id: %s", message_id)
        message = actor.message_with_options(args=({},)).copy(message_id=message_id)

        try:
            result = message.get_result(backend=result_backend, block=False)
            logger.info("Job completed for message_id: %s", message_id)
            return {
                "message_id": message_id,
                "status": "finished",
                "result": result,
            }
        except ResultMissing:
            logger.info("Result not yet available for message_id: %s", message_id)
            return {
                "message_id": message_id,
                "status": "pending",
                "message": "Job is being processed",
            }
        except ResultTimeout:
            logger.info("Result timeout for message_id: %s", message_id)
            return {
                "message_id": message_id,
                "status": "pending",
                "message": "Job is being processed",
            }
        except ResultFailure as e:
            logger.error("Job failed for message_id: %s - %s", message_id, e)
            return {
                "message_id": message_id,
                "status": "failed",
                "error": str(e),
            }
    except Exception as e:
        logger.error("Error in _try_get_result: %s", e, exc_info=True)
        return {
            "message_id": message_id,
            "status": "unknown",
            "error": str(e),
        }
