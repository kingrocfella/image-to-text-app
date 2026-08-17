"""Tests for RAG with PDF routes (queue-based API)."""

from io import BytesIO
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from pypdf import PdfWriter

from app.database import User
from app.utils import create_access_token, get_password_hash


def _valid_pdf_bytes() -> bytes:
    output = BytesIO()
    writer = PdfWriter()
    writer.add_blank_page(width=72, height=72)
    writer.write(output)
    return output.getvalue()


@pytest.mark.asyncio
async def test_rag_with_pdf_unauthorized(client: AsyncClient):
    """Test RAG with PDF without authentication."""
    pdf_content = _valid_pdf_bytes()
    pdf_file = BytesIO(pdf_content)

    response = await client.post(
        "/pdf/get/response",
        files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
        data={"query": "What is this about?", "model": "openai"},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_rag_with_pdf_invalid_model(
    client: AsyncClient, authenticated_user: dict
):
    """Test RAG with PDF with invalid model parameter."""
    pdf_content = _valid_pdf_bytes()
    pdf_file = BytesIO(pdf_content)

    response = await client.post(
        "/pdf/get/response",
        files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
        data={"query": "What is this about?", "model": "invalid-model"},
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "invalid" in data["detail"].lower()


@pytest.mark.asyncio
async def test_rag_with_pdf_missing_file(client: AsyncClient, authenticated_user: dict):
    """Test RAG with PDF without file or past_request_id."""
    response = await client.post(
        "/pdf/get/response",
        data={"query": "What is this about?", "model": "ollama"},
        headers=authenticated_user["headers"],
    )
    # Route returns 400 when neither PDF nor past_request_id is provided
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert (
        "either" in data["detail"].lower()
        or "must be provided" in data["detail"].lower()
    )


@pytest.mark.asyncio
async def test_rag_with_pdf_missing_query(
    client: AsyncClient, authenticated_user: dict
):
    """Test RAG with PDF without query parameter."""
    pdf_content = _valid_pdf_bytes()
    pdf_file = BytesIO(pdf_content)

    response = await client.post(
        "/pdf/get/response",
        files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
        headers=authenticated_user["headers"],
    )
    assert response.status_code == 422


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.verify_openai_password")
async def test_rag_with_pdf_incorrect_openai_pass(
    mock_verify_password,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test RAG with PDF with incorrect OpenAI password."""
    mock_verify_password.return_value = False

    pdf_content = _valid_pdf_bytes()
    pdf_file = BytesIO(pdf_content)

    response = await client.post(
        "/pdf/get/response",
        files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
        data={
            "query": "What is this about?",
            "model": "openai",
            "openai_pass": "wrong-password",
        },
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "password" in data["detail"].lower() or "incorrect" in data["detail"].lower()


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.verify_openai_password")
async def test_rag_with_pdf_missing_openai_pass(
    mock_verify_password,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test RAG with PDF with missing OpenAI password when model is openai."""
    mock_verify_password.return_value = False

    pdf_content = _valid_pdf_bytes()
    pdf_file = BytesIO(pdf_content)

    response = await client.post(
        "/pdf/get/response",
        files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
        data={"query": "What is this about?", "model": "openai"},
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "password" in data["detail"].lower() or "incorrect" in data["detail"].lower()


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_rag_with_pdf_both_pdf_and_past_request_id(
    mock_enqueue,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test RAG with PDF when both PDF and past_request_id are provided."""
    pdf_content = _valid_pdf_bytes()
    pdf_file = BytesIO(pdf_content)
    pdf_file.seek(0)

    response = await client.post(
        "/pdf/get/response",
        files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
        data={
            "query": "What is this about?",
            "model": "ollama",
            "past_request_id": "some-id",
        },
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "both" in data["detail"].lower() or "cannot" in data["detail"].lower()
    mock_enqueue.assert_not_called()


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.Path.mkdir")
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_rag_with_pdf_success_with_pdf(
    mock_enqueue,
    _mock_mkdir,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test successful RAG job enqueue with PDF file."""
    mock_enqueue.return_value = "test-job-id-123"

    pdf_content = _valid_pdf_bytes()
    pdf_file = BytesIO(pdf_content)
    pdf_file.seek(0)

    with patch("app.routes.rag_with_pdf.tempfile.NamedTemporaryFile") as mock_temp:
        mock_temp.return_value.__enter__.return_value.name = "/tmp/test.pdf"

        response = await client.post(
            "/pdf/get/response",
            files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
            data={"query": "What is this about?", "model": "ollama"},
            headers=authenticated_user["headers"],
        )

    assert response.status_code == 202
    data = response.json()
    assert "message_id" in data
    assert data["message_id"] == "test-job-id-123"
    assert data["status"] == "queued"
    mock_enqueue.assert_called_once()


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_rag_with_pdf_success_with_past_request_id(
    mock_enqueue,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test successful RAG job enqueue with past_request_id."""
    mock_enqueue.return_value = "test-job-id-456"

    response = await client.post(
        "/pdf/get/response",
        data={
            "query": "What is this about?",
            "model": "ollama",
            "past_request_id": "existing-request-id",
        },
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 202
    data = response.json()
    assert "message_id" in data
    assert data["message_id"] == "test-job-id-456"
    assert data["status"] == "queued"
    mock_enqueue.assert_called_once()

    # Verify job data includes past_request_id
    call_args = mock_enqueue.call_args[0][0]
    assert call_args["past_request_id"] == "existing-request-id"
    assert "pdf_file_path" not in call_args


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.verify_openai_password")
@patch("app.routes.rag_with_pdf.Path.mkdir")
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_rag_with_pdf_success_with_openai(
    mock_enqueue,
    _mock_mkdir,
    mock_verify_password,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test successful RAG job enqueue with OpenAI model."""
    mock_verify_password.return_value = True
    mock_enqueue.return_value = "test-job-id-789"

    pdf_content = _valid_pdf_bytes()
    pdf_file = BytesIO(pdf_content)
    pdf_file.seek(0)

    with patch("app.routes.rag_with_pdf.tempfile.NamedTemporaryFile") as mock_temp:
        mock_temp.return_value.__enter__.return_value.name = "/tmp/test.pdf"

        response = await client.post(
            "/pdf/get/response",
            files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
            data={
                "query": "What is this about?",
                "model": "openai",
                "openai_pass": "test-pass",
            },
            headers=authenticated_user["headers"],
        )

    assert response.status_code == 202
    data = response.json()
    assert data["message_id"] == "test-job-id-789"
    mock_enqueue.assert_called_once()

    # Authorization is decided by the API; the shared secret never enters Redis.
    call_args = mock_enqueue.call_args[0][0]
    assert call_args["model"] == "openai"
    assert "openai_pass" not in call_args


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.Path.mkdir")
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_rag_with_pdf_empty_file(
    mock_enqueue,
    _mock_mkdir,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test RAG with PDF using empty file."""
    empty_file = BytesIO(b"")

    with patch("app.routes.rag_with_pdf.tempfile.NamedTemporaryFile") as mock_temp:
        mock_temp.return_value.__enter__.return_value.name = "/tmp/empty.pdf"

        response = await client.post(
            "/pdf/get/response",
            files={"pdf": ("empty.pdf", empty_file, "application/pdf")},
            data={"query": "What is this about?", "model": "ollama"},
            headers=authenticated_user["headers"],
        )

    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "empty" in data["detail"].lower()
    mock_enqueue.assert_not_called()


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_rag_with_pdf_enqueue_failure(
    mock_enqueue,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Test RAG with PDF when enqueue fails."""
    mock_enqueue.side_effect = Exception("Redis connection failed")

    response = await client.post(
        "/pdf/get/response",
        data={
            "query": "What is this about?",
            "model": "ollama",
            "past_request_id": "existing-request-id",
        },
        headers=authenticated_user["headers"],
    )

    assert response.status_code == 500
    data = response.json()
    assert "detail" in data
    assert "failed" in data["detail"].lower()


@pytest.mark.asyncio
async def test_rag_with_pdf_unverified_user(
    client: AsyncClient, db_session, test_user_data
):
    """Test RAG with PDF with unverified user."""
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

    pdf_content = b"%PDF-1.4\nfake pdf content"
    pdf_file = BytesIO(pdf_content)

    response = await client.post(
        "/pdf/get/response",
        files={"pdf": ("test.pdf", pdf_file, "application/pdf")},
        data={"query": "What is this about?", "model": "openai"},
        headers=headers,
    )

    assert response.status_code == 403
    data = response.json()
    assert "detail" in data
    assert "verified" in data["detail"].lower()
