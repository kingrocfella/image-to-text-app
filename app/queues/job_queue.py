"""Dramatiq queue utilities for background job processing."""

import asyncio
import json
import os
from typing import Any, Dict

import dramatiq
import redis

from app.database.redis import get_redis_broker, get_redis_url, get_result_backend
from app.utils.file_utils import delete_temp_file
from app.utils.logger import logger
from app.workers import (
    process_image_job_sync,
    process_rag_job_async,
    process_sound_job_sync,
)

redis_broker = get_redis_broker()
result_backend = get_result_backend()

# Redis client for job type tracking
_redis_client: redis.Redis | None = None


def _get_redis_client() -> redis.Redis:
    """Get Redis client for job type tracking."""
    global _redis_client  # pylint: disable=global-statement
    if _redis_client is None:
        _redis_client = redis.from_url(get_redis_url())
    return _redis_client


# Job type constants
JOB_TYPE_RAG = "rag"
JOB_TYPE_SOUND = "sound"
JOB_TYPE_IMAGE = "image"
JOB_METADATA_KEY_PREFIX = "job:metadata:"
JOB_TYPE_TTL = 86400 * int(os.getenv("JOB_TYPE_TTL_DAYS", "7"))
DELETED_ACCOUNT_KEY_PREFIX = "account:deleted:"

# Set the broker for dramatiq
dramatiq.set_broker(redis_broker)


def _store_job_metadata(message_id: str, job_type: str, owner_user_id: str) -> None:
    """Store the minimum metadata required to authorize later job access."""
    if not owner_user_id:
        raise ValueError("A job owner is required")
    client = _get_redis_client()
    key = f"{JOB_METADATA_KEY_PREFIX}{message_id}"
    metadata = json.dumps({"job_type": job_type, "owner_user_id": owner_user_id})
    client.setex(key, JOB_TYPE_TTL, metadata)
    logger.debug("Stored owner-bound job metadata for message_id: %s", message_id)


def _assert_account_active(owner_user_id: str) -> None:
    if _get_redis_client().get(f"{DELETED_ACCOUNT_KEY_PREFIX}{owner_user_id}"):
        raise RuntimeError("Account is no longer active")


def mark_account_deleted_and_purge_jobs(owner_user_id: str) -> None:
    """Block in-flight work and erase discoverable metadata/results for an account."""
    client = _get_redis_client()
    client.setex(f"{DELETED_ACCOUNT_KEY_PREFIX}{owner_user_id}", JOB_TYPE_TTL, "1")
    keys_to_delete: set[str | bytes] = set()
    for key in client.scan_iter(match=f"{JOB_METADATA_KEY_PREFIX}*"):
        raw_metadata = client.get(key)
        if isinstance(raw_metadata, bytes):
            raw_metadata = raw_metadata.decode("utf-8")
        try:
            metadata = json.loads(raw_metadata) if isinstance(raw_metadata, str) else {}
        except json.JSONDecodeError:
            metadata = {}
        if metadata.get("owner_user_id") != owner_user_id:
            continue
        keys_to_delete.add(key)
        key_text = key.decode("utf-8") if isinstance(key, bytes) else key
        message_id = key_text.removeprefix(JOB_METADATA_KEY_PREFIX)
        keys_to_delete.update(client.scan_iter(match=f"*{message_id}*"))
    if keys_to_delete:
        client.delete(*keys_to_delete)


# =============================================================================
# RAG PDF Processing
# =============================================================================


@dramatiq.actor(store_results=True, max_retries=3, time_limit=600000)
def process_rag_job(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process a RAG PDF job using Dramatiq."""
    try:
        _assert_account_active(str(job_data["user_id"]))
        logger.info("Starting RAG job processing")
        result = asyncio.run(process_rag_job_async(job_data))
        _assert_account_active(str(job_data["user_id"]))
        logger.info("RAG job completed successfully: %s", result.get("request_id"))
        return result
    except Exception as e:
        logger.error("Error processing RAG job: %s", e, exc_info=True)
        raise
    finally:
        delete_temp_file(job_data.get("pdf_file_path"), silent=True)


def enqueue_rag_job(job_data: Dict[str, Any]) -> str:
    """Enqueue a RAG PDF processing job."""
    message = process_rag_job.send(job_data)
    _store_job_metadata(message.message_id, JOB_TYPE_RAG, str(job_data["user_id"]))
    logger.info("Enqueued RAG job with message ID: %s", message.message_id)
    return message.message_id


# =============================================================================
# Sound-to-Text Processing
# =============================================================================


@dramatiq.actor(store_results=True, max_retries=3, time_limit=300000)
def process_sound_job(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process a sound-to-text job using Dramatiq."""
    try:
        _assert_account_active(str(job_data["user_id"]))
        logger.info("Starting sound-to-text job processing")
        result = process_sound_job_sync(job_data)
        _assert_account_active(str(job_data["user_id"]))
        logger.info("Sound-to-text job completed successfully")
        return result
    except Exception as e:
        logger.error("Error processing sound-to-text job: %s", e, exc_info=True)
        raise


def enqueue_sound_job(job_data: Dict[str, Any]) -> str:
    """Enqueue a sound-to-text processing job."""
    message = process_sound_job.send(job_data)
    _store_job_metadata(message.message_id, JOB_TYPE_SOUND, str(job_data["user_id"]))
    logger.info("Enqueued sound-to-text job with message ID: %s", message.message_id)
    return message.message_id


# =============================================================================
# Image-to-Text Processing
# =============================================================================


@dramatiq.actor(store_results=True, max_retries=3, time_limit=300000)
def process_image_job(job_data: Dict[str, Any]) -> Dict[str, Any]:
    """Process an image-to-text job using Dramatiq."""
    try:
        _assert_account_active(str(job_data["user_id"]))
        logger.info("Starting image-to-text job processing")
        result = process_image_job_sync(job_data)
        _assert_account_active(str(job_data["user_id"]))
        logger.info("Image-to-text job completed successfully")
        return result
    except Exception as e:
        logger.error("Error processing image-to-text job: %s", e, exc_info=True)
        raise


def enqueue_image_job(job_data: Dict[str, Any]) -> str:
    """Enqueue an image-to-text processing job."""
    message = process_image_job.send(job_data)
    _store_job_metadata(message.message_id, JOB_TYPE_IMAGE, str(job_data["user_id"]))
    logger.info("Enqueued image-to-text job with message ID: %s", message.message_id)
    return message.message_id
