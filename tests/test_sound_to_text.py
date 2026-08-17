"""Tests for sound to text conversion routes (queue-based API)."""

from io import BytesIO
import wave
from unittest.mock import patch

import pytest
from httpx import AsyncClient

from app.database import User
from app.utils import create_access_token, get_password_hash


def _wav_bytes() -> BytesIO:
    output = BytesIO()
    with wave.open(output, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(8000)
        wav_file.writeframes(b"\x00\x00" * 80)
    output.seek(0)
    return output


@pytest.mark.asyncio
async def test_convert_sound_unauthorized(client: AsyncClient):
    """Test sound conversion without authentication."""
    audio_bytes = _wav_bytes()

    response = await client.post(
        "/convert/sound/text", files={"file": ("test.wav", audio_bytes, "audio/wav")}
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_convert_sound_invalid_file_type(
    client: AsyncClient, authenticated_user: dict
):
    """Test sound conversion with invalid file type."""
    text_file = BytesIO(b"This is not an audio file")

    response = await client.post(
        "/convert/sound/text",
        files={"file": ("test.txt", text_file, "text/plain")},
        headers=authenticated_user["headers"],
    )
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "invalid" in data["detail"].lower()


@pytest.mark.asyncio
async def test_convert_sound_missing_file(
    client: AsyncClient, authenticated_user: dict
):
    """Test sound conversion without file."""
    response = await client.post(
        "/convert/sound/text", headers=authenticated_user["headers"]
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@patch("app.routes.sound_to_text.Path.mkdir")
@patch("app.routes.sound_to_text.enqueue_sound_job")
async def test_convert_sound_success(
    mock_enqueue,
    _mock_mkdir,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test successful sound-to-text job enqueue."""
    mock_enqueue.return_value = "test-job-id-123"

    audio_bytes = _wav_bytes()

    with patch("app.routes.sound_to_text.tempfile.NamedTemporaryFile") as mock_temp:
        mock_temp.return_value.__enter__.return_value.name = "/tmp/test.wav"

        response = await client.post(
            "/convert/sound/text",
            files={"file": ("test.wav", audio_bytes, "audio/wav")},
            headers=authenticated_user["headers"],
        )

    assert response.status_code == 202
    data = response.json()
    assert "message_id" in data
    assert data["message_id"] == "test-job-id-123"
    assert data["status"] == "queued"
    mock_enqueue.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.sound_to_text.Path.mkdir")
@patch("app.routes.sound_to_text.enqueue_sound_job")
async def test_convert_sound_empty_file(
    mock_enqueue,
    _mock_mkdir,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test sound conversion with empty file."""
    empty_file = BytesIO(b"")

    with patch("app.routes.sound_to_text.tempfile.NamedTemporaryFile") as mock_temp:
        mock_temp.return_value.__enter__.return_value.name = "/tmp/empty.wav"

        response = await client.post(
            "/convert/sound/text",
            files={"file": ("empty.wav", empty_file, "audio/wav")},
            headers=authenticated_user["headers"],
        )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "empty" in data["detail"].lower()
    mock_enqueue.assert_not_called()


@pytest.mark.asyncio
@patch("app.routes.sound_to_text.enqueue_sound_job")
async def test_convert_sound_enqueue_failure(
    mock_enqueue,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test sound conversion when enqueue fails."""
    mock_enqueue.side_effect = Exception("Redis connection failed")

    audio_bytes = _wav_bytes()

    with patch("app.routes.sound_to_text.Path.mkdir"):
        with patch("app.routes.sound_to_text.tempfile.NamedTemporaryFile") as mock_temp:
            mock_temp.return_value.__enter__.return_value.name = "/tmp/test.wav"

            response = await client.post(
                "/convert/sound/text",
                files={"file": ("test.wav", audio_bytes, "audio/wav")},
                headers=authenticated_user["headers"],
            )

    assert response.status_code == 500
    data = response.json()
    assert "detail" in data
    assert "failed" in data["detail"].lower()


@pytest.mark.asyncio
async def test_convert_sound_unverified_user(
    client: AsyncClient, db_session, test_user_data
):
    """Test sound conversion with unverified user."""
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

    audio_bytes = BytesIO(b"fake audio content")

    response = await client.post(
        "/convert/sound/text",
        files={"file": ("test.wav", audio_bytes, "audio/wav")},
        headers=headers,
    )
    assert response.status_code == 403
    data = response.json()
    assert "detail" in data
    assert "verified" in data["detail"].lower()


@pytest.mark.asyncio
@patch("app.routes.sound_to_text.Path.mkdir")
@patch("app.routes.sound_to_text.enqueue_sound_job")
async def test_convert_sound_different_formats(
    mock_enqueue,
    _mock_mkdir,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test sound conversion with different audio formats."""
    mock_enqueue.return_value = "test-job-id"

    formats = [
        ("wav", "audio/wav"),
        ("mp3", "audio/mpeg"),
        ("m4a", "audio/mp4"),
    ]

    for ext, mime_type in formats:
        audio_bytes = BytesIO(b"fake audio content")

        with patch("app.routes.sound_to_text.tempfile.NamedTemporaryFile") as mock_temp:
            mock_temp.return_value.__enter__.return_value.name = f"/tmp/test.{ext}"

            response = await client.post(
                "/convert/sound/text",
                files={"file": (f"test.{ext}", audio_bytes, mime_type)},
                headers=authenticated_user["headers"],
            )
            # Should either succeed (202) or fail validation (400)
            assert response.status_code in [202, 400]
