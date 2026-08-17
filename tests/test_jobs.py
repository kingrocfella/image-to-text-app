"""Tests for unified job status endpoint."""

from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.queues import JOB_TYPE_IMAGE, JOB_TYPE_RAG, JOB_TYPE_SOUND, JobNotFoundError


@pytest.mark.asyncio
@patch("app.routes.jobs.get_job_status")
async def test_get_job_status_pending(
    mock_get_status,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test getting job status when job is pending."""
    mock_get_status.return_value = {
        "status": "pending",
        "message": "Job is being processed",
        "job_type": JOB_TYPE_RAG,
    }

    response = await client.get(
        "/job/test-job-id",
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "pending"
    mock_get_status.assert_called_once_with(
        "test-job-id", str(authenticated_user["user"].id)
    )


@pytest.mark.asyncio
@patch("app.routes.jobs.get_job_status")
async def test_get_job_status_rag_finished(
    mock_get_status,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test getting RAG job status when job is finished."""
    mock_get_status.return_value = {
        "status": "finished",
        "job_type": JOB_TYPE_RAG,
        "result": {
            "content": "This is the answer from the PDF.",
            "request_id": "req-123",
        },
    }

    response = await client.get(
        "/job/test-job-id",
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "This is the answer from the PDF."
    assert data["request_id"] == "req-123"


@pytest.mark.asyncio
@patch("app.routes.jobs.get_job_status")
async def test_get_job_status_sound_finished(
    mock_get_status,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test getting sound job status when job is finished."""
    mock_get_status.return_value = {
        "status": "finished",
        "job_type": JOB_TYPE_SOUND,
        "result": {
            "content": "This is the transcribed text.",
            "filename": "test.wav",
        },
    }

    response = await client.get(
        "/job/test-job-id",
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "This is the transcribed text."
    assert data["filename"] == "test.wav"


@pytest.mark.asyncio
@patch("app.routes.jobs.get_job_status")
async def test_get_job_status_image_finished(
    mock_get_status,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test getting image job status when job is finished."""
    mock_get_status.return_value = {
        "status": "finished",
        "job_type": JOB_TYPE_IMAGE,
        "result": {
            "content": "Extracted text from image",
            "filename": "test.png",
            "segments_count": 5,
        },
    }

    response = await client.get(
        "/job/test-job-id",
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["content"] == "Extracted text from image"
    assert data["filename"] == "test.png"
    assert data["segments_count"] == 5


@pytest.mark.asyncio
@patch("app.routes.jobs.get_job_status")
async def test_get_job_status_failed(
    mock_get_status,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test getting job status when job has failed."""
    mock_get_status.return_value = {
        "status": "failed",
        "error": "Processing failed",
        "job_type": JOB_TYPE_RAG,
    }

    response = await client.get(
        "/job/test-job-id",
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "failed"
    assert data["error"] == "Processing failed"


@pytest.mark.asyncio
@patch("app.routes.jobs.get_job_status")
async def test_get_job_status_unknown(
    mock_get_status,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test getting job status when job type is unknown."""
    mock_get_status.return_value = {
        "status": "unknown",
        "error": "Job not found",
        "job_type": "unknown",
    }

    response = await client.get(
        "/job/test-job-id",
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "unknown"


@pytest.mark.asyncio
@patch("app.routes.jobs.get_job_status")
async def test_get_job_status_error(
    mock_get_status,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test getting job status when an error occurs."""
    mock_get_status.side_effect = Exception("Redis connection failed")

    response = await client.get(
        "/job/test-job-id",
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 500
    data = response.json()
    assert "detail" in data
    assert "failed" in data["detail"].lower()


@pytest.mark.asyncio
async def test_get_job_status_unauthorized(client: AsyncClient):
    """Test getting job status without authentication."""
    response = await client.get("/job/test-job-id")
    assert response.status_code == 403


@pytest.mark.asyncio
@patch("app.routes.jobs.get_job_status")
async def test_get_job_status_hides_jobs_not_owned_by_caller(
    mock_get_status,
    client: AsyncClient,
    authenticated_user: dict,
):
    """A missing or differently owned job must be indistinguishable."""
    mock_get_status.side_effect = JobNotFoundError("Job not found")

    response = await client.get(
        "/job/another-users-job",
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Job not found."}
