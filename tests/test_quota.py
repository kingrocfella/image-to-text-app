"""Per-user monthly allowances and the account endpoint."""

from io import BytesIO
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import override_settings
from app.database import JobRun, UsageCounter
from tests.test_rag_with_pdf import _valid_pdf_bytes


async def _used(db: AsyncSession, kind: str) -> int:
    count = (
        await db.execute(select(UsageCounter.count).where(UsageCounter.kind == kind))
    ).scalar_one_or_none()
    return int(count or 0)


async def _ask(client: AsyncClient, headers: dict, model: str):
    with (
        patch("app.routes.rag_with_pdf.Path.mkdir"),
        patch("app.routes.rag_with_pdf.tempfile.NamedTemporaryFile") as mock_temp,
        patch("app.routes.rag_with_pdf.delete_temp_file"),
    ):
        mock_temp.return_value.__enter__.return_value.name = "/tmp/test.pdf"
        return await client.post(
            "/v1/pdf/get/response",
            files={"pdf": ("test.pdf", BytesIO(_valid_pdf_bytes()), "application/pdf")},
            data={"query": "What is this about?", "model": model},
            headers=headers,
        )


@pytest.mark.asyncio
async def test_me_reports_offered_models_and_usage(
    client: AsyncClient, authenticated_user: dict
):
    response = await client.get("/v1/me", headers=authenticated_user["headers"])

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "test@example.com"
    # A free account: the local model and Gemini. DeepSeek has no key in the
    # test configuration; Claude and OpenAI are configured but need Pro.
    assert body["pro"] is False
    assert body["models"] == ["ollama", "gemini"]
    assert body["pro_models"] == ["claude", "openai"]
    assert body["login_methods"] == ["password"]
    assert body["purchases_available"] is True
    assert body["pro_limits"]["cloud_model"] == 300
    assert body["usage"]["cloud_model"] == {"used": 0, "limit": 50}
    assert set(body["usage"]) == {"image", "sound", "pdf", "cloud_model"}


@pytest.mark.asyncio
async def test_no_cloud_models_are_offered_when_the_allowance_is_zero(
    client: AsyncClient, authenticated_user: dict
):
    with override_settings(quota_cloud_model_monthly=0):
        response = await client.get("/v1/me", headers=authenticated_user["headers"])
        refused = await _ask(client, authenticated_user["headers"], "gemini")

    assert response.json()["models"] == ["ollama"]
    # Gemini exists for Pro, so a free account is sent to the paywall.
    assert refused.status_code == 402


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_cloud_model_question_spends_both_allowances_and_records_the_job(
    mock_enqueue,
    client: AsyncClient,
    authenticated_user: dict,
    db_session: AsyncSession,
):
    mock_enqueue.return_value = "job-1"

    response = await _ask(client, authenticated_user["headers"], "gemini")

    assert response.status_code == 202
    assert await _used(db_session, "pdf") == 1
    assert await _used(db_session, "cloud_model") == 1
    job = (await db_session.execute(select(JobRun))).scalar_one()
    assert (job.message_id, job.job_type, job.status) == ("job-1", "rag", "queued")


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_local_model_question_does_not_spend_the_cloud_allowance(
    mock_enqueue,
    client: AsyncClient,
    authenticated_user: dict,
    db_session: AsyncSession,
):
    mock_enqueue.return_value = "job-1"

    response = await _ask(client, authenticated_user["headers"], "ollama")

    assert response.status_code == 202
    assert await _used(db_session, "pdf") == 1
    assert await _used(db_session, "cloud_model") == 0


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_exhausted_cloud_allowance_refuses_and_spends_nothing(
    mock_enqueue,
    client: AsyncClient,
    authenticated_user: dict,
    db_session: AsyncSession,
):
    mock_enqueue.side_effect = ["job-1", "job-2"]
    headers = authenticated_user["headers"]

    with override_settings(quota_cloud_model_monthly=1):
        first = await _ask(client, headers, "gemini")
        second = await _ask(client, headers, "gemini")
        local = await _ask(client, headers, "ollama")

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.headers["x-quota-exceeded"] == "cloud_model"
    assert "Upgrade to ScanGenAI Pro" in second.json()["detail"]
    assert second.headers["x-upgrade-available"] == "true"
    assert mock_enqueue.call_count == 2
    # The refused request gave its PDF unit back; the local one then spent one.
    assert await _used(db_session, "pdf") == 2
    assert await _used(db_session, "cloud_model") == 1
    assert local.status_code == 202


@pytest.mark.asyncio
@patch("app.routes.rag_with_pdf.enqueue_rag_job")
async def test_a_failed_enqueue_does_not_spend_the_allowance(
    mock_enqueue,
    client: AsyncClient,
    authenticated_user: dict,
    db_session: AsyncSession,
):
    mock_enqueue.side_effect = RuntimeError("redis down")

    response = await _ask(client, authenticated_user["headers"], "ollama")

    assert response.status_code == 500
    assert await _used(db_session, "pdf") == 0


@pytest.mark.asyncio
@patch("app.routes.image_to_text.Path.mkdir")
@patch("app.routes.image_to_text.enqueue_image_job")
async def test_image_allowance_is_enforced_per_user(
    mock_enqueue,
    _mock_mkdir,
    client: AsyncClient,
    authenticated_user: dict,
    mock_image_file,
):
    mock_enqueue.return_value = "job-1"
    filename, content, mime = mock_image_file

    async def scan():
        content.seek(0)
        with (
            patch("app.routes.image_to_text.tempfile.NamedTemporaryFile") as mock_temp,
            patch("app.routes.image_to_text.delete_temp_file"),
        ):
            mock_temp.return_value.__enter__.return_value.name = "/tmp/test.png"
            return await client.post(
                "/v1/convert/image/text",
                files={"image": (filename, content, mime)},
                headers=authenticated_user["headers"],
            )

    with override_settings(quota_image_monthly=1):
        first = await scan()
        second = await scan()

    assert first.status_code == 202
    assert second.status_code == 429
    assert second.headers["x-quota-exceeded"] == "image"
