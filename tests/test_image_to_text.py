"""Tests for image to text conversion routes."""

from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from PIL import Image

from app.database import User
from app.utils import create_access_token, get_password_hash


@pytest.mark.asyncio
async def test_convert_image_unauthorized(client: AsyncClient):
    """Test image conversion without authentication."""
    img = Image.new("RGB", (100, 100), color="red")
    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    response = await client.post(
        "/convert/image/text", files={"image": ("test.png", img_bytes, "image/png")}
    )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_convert_image_invalid_file_type(
    client: AsyncClient, authenticated_user: dict
):
    """Test image conversion with invalid file type."""
    text_file = BytesIO(b"This is not an image")

    response = await client.post(
        "/convert/image/text",
        files={"image": ("test.txt", text_file, "text/plain")},
        headers=authenticated_user["headers"],
    )
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "invalid" in data["detail"].lower()


@pytest.mark.asyncio
async def test_convert_image_invalid_mime_type(
    client: AsyncClient, authenticated_user: dict
):
    """Test image conversion with invalid MIME type."""
    img = Image.new("RGB", (100, 100), color="red")
    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    response = await client.post(
        "/convert/image/text",
        files={"image": ("test.png", img_bytes, "application/pdf")},
        headers=authenticated_user["headers"],
    )
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "mime" in data["detail"].lower() or "invalid" in data["detail"].lower()


@pytest.mark.asyncio
async def test_convert_image_missing_file(
    client: AsyncClient, authenticated_user: dict
):
    """Test image conversion without file."""
    response = await client.post(
        "/convert/image/text", headers=authenticated_user["headers"]
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_convert_image_job_queued(
    client: AsyncClient, authenticated_user: dict, tmp_path: Path
):
    """Test successful image-to-text job queueing."""
    with (
        patch("app.routes.image_to_text.enqueue_image_job") as mock_enqueue,
        patch("app.routes.image_to_text.SHARED_IMAGE_DIR", tmp_path),
    ):
        mock_enqueue.return_value = "test-job-id-123"

        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        response = await client.post(
            "/convert/image/text",
            files={"image": ("test.png", img_bytes, "image/png")},
            headers=authenticated_user["headers"],
        )
        assert response.status_code == 202
        data = response.json()
        assert data["message_id"] == "test-job-id-123"
        assert data["status"] == "queued"
        assert "message" in data

        # Verify enqueue was called
        mock_enqueue.assert_called_once()
        call_args = mock_enqueue.call_args[0][0]
        assert "image_file_path" in call_args
        assert call_args["filename"] == "test.png"


@pytest.mark.asyncio
async def test_convert_image_different_formats(
    client: AsyncClient, authenticated_user: dict, tmp_path: Path
):
    """Test image queueing with different image formats."""
    formats = [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
    ]

    for format_name, mime_type in formats:
        with (
            patch("app.routes.image_to_text.enqueue_image_job") as mock_enqueue,
            patch("app.routes.image_to_text.SHARED_IMAGE_DIR", tmp_path),
        ):
            mock_enqueue.return_value = f"test-job-id-{format_name}"

            img = Image.new("RGB", (100, 100), color="red")
            img_bytes = BytesIO()
            img.save(img_bytes, format=format_name)
            img_bytes.seek(0)

            response = await client.post(
                "/convert/image/text",
                files={"image": (f"test.{format_name.lower()}", img_bytes, mime_type)},
                headers=authenticated_user["headers"],
            )
            assert response.status_code == 202
            data = response.json()
            assert data["status"] == "queued"


@pytest.mark.asyncio
async def test_convert_image_unverified_user(
    client: AsyncClient, db_session, test_user_data
):
    """Test image conversion with unverified user."""

    user = User(
        name=test_user_data["name"],
        email=test_user_data["email"],
        hashed_password=get_password_hash(test_user_data["password"]),
        is_verified=False,
        verification_token=None,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    access_token = create_access_token(data={"sub": str(user.id)})
    headers = {"Authorization": f"Bearer {access_token}"}

    img = Image.new("RGB", (100, 100), color="red")
    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)

    response = await client.post(
        "/convert/image/text",
        files={"image": ("test.png", img_bytes, "image/png")},
        headers=headers,
    )
    assert response.status_code == 403
    data = response.json()
    assert "detail" in data
    assert "verified" in data["detail"].lower()


@pytest.mark.asyncio
async def test_convert_image_enqueue_error(
    client: AsyncClient, authenticated_user: dict, tmp_path: Path
):
    """Test image conversion when enqueueing fails."""
    with (
        patch("app.routes.image_to_text.enqueue_image_job") as mock_enqueue,
        patch("app.routes.image_to_text.SHARED_IMAGE_DIR", tmp_path),
    ):
        mock_enqueue.side_effect = Exception("Queue service unavailable")

        img = Image.new("RGB", (100, 100), color="red")
        img_bytes = BytesIO()
        img.save(img_bytes, format="PNG")
        img_bytes.seek(0)

        response = await client.post(
            "/convert/image/text",
            files={"image": ("test.png", img_bytes, "image/png")},
            headers=authenticated_user["headers"],
        )
        assert response.status_code == 500
        data = response.json()
        assert "detail" in data
        assert "enqueue" in data["detail"].lower() or "failed" in data["detail"].lower()


@pytest.mark.asyncio
async def test_convert_image_empty_file(
    client: AsyncClient, authenticated_user: dict, tmp_path: Path
):
    """Test image conversion with empty file content."""
    with (
        patch("app.routes.image_to_text.enqueue_image_job") as mock_enqueue,
        patch("app.routes.image_to_text.SHARED_IMAGE_DIR", tmp_path),
    ):
        empty_bytes = BytesIO(b"")

        response = await client.post(
            "/convert/image/text",
            files={"image": ("test.png", empty_bytes, "image/png")},
            headers=authenticated_user["headers"],
        )
        # Should fail validation or return 400
        assert response.status_code in [400, 422]

        # Enqueue should not be called for empty files
        mock_enqueue.assert_not_called()
