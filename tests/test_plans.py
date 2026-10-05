"""Free and Pro: which models each plan may use, and how the app unlocks Pro."""

from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import ConfigError, get_settings, load_settings, override_settings
from app.utils.constants import available_models, pro_only_models
from tests.conftest import requires_postgres
from tests.test_config import BASE
from tests.test_rag_with_pdf import _valid_pdf_bytes


async def _ask(client: AsyncClient, headers: dict, model: str):
    with (
        patch("app.routes.rag_with_pdf.Path.mkdir"),
        patch("app.routes.rag_with_pdf.tempfile.NamedTemporaryFile") as mock_temp,
        patch("app.routes.rag_with_pdf.delete_temp_file"),
        patch("app.routes.rag_with_pdf.enqueue_rag_job") as enqueue,
    ):
        enqueue.side_effect = lambda _data: f"job-{model}-{datetime.now().timestamp()}"
        mock_temp.return_value.__enter__.return_value.name = "/tmp/test.pdf"
        return await client.post(
            "/v1/pdf/get/response",
            files={"pdf": ("test.pdf", BytesIO(_valid_pdf_bytes()), "application/pdf")},
            data={"query": "What is this about?", "model": model},
            headers=headers,
        )


def test_the_catalogue_per_plan():
    settings = get_settings()
    assert available_models(settings, pro=False) == ["ollama", "gemini"]
    assert available_models(settings, pro=True) == [
        "ollama",
        "gemini",
        "claude",
        "openai",
    ]
    assert pro_only_models(settings, pro=False) == ["claude", "openai"]
    assert pro_only_models(settings, pro=True) == []


def test_openai_can_never_be_configured_as_a_free_model():
    """Owner decision: OpenAI is strictly for subscribers."""
    with pytest.raises(ConfigError) as raised:
        load_settings({**BASE, "FREE_CLOUD_MODELS": "gemini,openai"})
    assert "must not include openai" in str(raised.value)
    with pytest.raises(ConfigError):
        load_settings({**BASE, "FREE_CLOUD_MODELS": "gemini,llama"})


def test_a_model_without_its_provider_key_is_offered_to_nobody():
    with override_settings(anthropic_api_key=""):
        assert "claude" not in available_models(get_settings(), pro=True)
    # Every PDF question needs embeddings, whichever model answers.
    with override_settings(openai_api_key=""):
        assert available_models(get_settings(), pro=True) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["openai", "claude"])
async def test_a_free_account_is_sent_to_the_paywall_for_a_pro_model(
    model, client: AsyncClient, authenticated_user: dict
):
    response = await _ask(client, authenticated_user["headers"], model)

    assert response.status_code == 402
    assert "ScanGenAI Pro" in response.json()["detail"]


@pytest.mark.asyncio
async def test_a_free_account_can_use_a_free_cloud_model(
    client: AsyncClient, authenticated_user: dict
):
    response = await _ask(client, authenticated_user["headers"], "gemini")
    assert response.status_code == 202


@requires_postgres
@pytest.mark.asyncio
async def test_a_grant_unlocks_every_model_and_the_pro_allowances(
    client: AsyncClient, authenticated_user: dict, db_session: AsyncSession
):
    user = authenticated_user["user"]
    user.pro_until = datetime.now(timezone.utc) + timedelta(days=30)
    await db_session.commit()
    headers = authenticated_user["headers"]

    me = (await client.get("/v1/me", headers=headers)).json()
    openai = await _ask(client, headers, "openai")
    claude = await _ask(client, headers, "claude")

    assert (me["pro"], me["pro_source"]) == (True, "grant")
    assert me["models"] == ["ollama", "gemini", "claude", "openai"]
    assert me["pro_models"] == []
    assert me["usage"]["cloud_model"]["limit"] == 300
    assert openai.status_code == claude.status_code == 202


@requires_postgres
@pytest.mark.asyncio
async def test_buying_pro_unlocks_it_and_restore_is_idempotent(
    client: AsyncClient, authenticated_user: dict
):
    """BILLING_PROVIDER=dev in tests: receipts are `product:transaction[:flag]`."""
    headers = authenticated_user["headers"]

    bought = await client.post(
        "/v1/billing/purchases/verify",
        json={"platform": "ios", "receipt": "scangenai_pro_monthly:tx-1"},
        headers=headers,
    )
    assert bought.status_code == 200
    body = bought.json()
    assert (body["pro"], body["pro_source"]) == (True, "subscription")
    assert body["pro_product_id"] == "scangenai_pro_monthly"
    assert "openai" in body["models"]

    restored = await client.post(
        "/v1/billing/purchases/recover",
        json={
            "platform": "ios",
            "receipts": ["scangenai_pro_monthly:tx-1", "garbage"],
        },
        headers=headers,
    )
    assert restored.status_code == 200
    assert restored.json()["pro"] is True


@requires_postgres
@pytest.mark.asyncio
async def test_wrong_product_expired_and_revoked_receipts_do_not_unlock(
    client: AsyncClient, authenticated_user: dict
):
    headers = authenticated_user["headers"]

    wrong = await client.post(
        "/v1/billing/purchases/verify",
        json={"platform": "android", "receipt": "noalibi_pro_yearly:tx-9"},
        headers=headers,
    )
    assert wrong.status_code == 400
    for flag in ("expired", "revoked"):
        response = await client.post(
            "/v1/billing/purchases/verify",
            json={
                "platform": "android",
                "receipt": f"scangenai_pro_yearly:tx-{flag}:{flag}",
            },
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["pro"] is False


@pytest.mark.asyncio
async def test_purchases_are_refused_while_billing_is_off(
    client: AsyncClient, authenticated_user: dict
):
    from app.services.billing import service

    service.billing_deps.cache_clear()
    try:
        with override_settings(billing_provider="off"):
            me = await client.get("/v1/me", headers=authenticated_user["headers"])
            refused = await client.post(
                "/v1/billing/purchases/verify",
                json={"platform": "ios", "receipt": "scangenai_pro_monthly:tx-1"},
                headers=authenticated_user["headers"],
            )
    finally:
        service.billing_deps.cache_clear()

    assert me.json()["purchases_available"] is False
    assert refused.status_code == 503


@pytest.mark.asyncio
async def test_billing_requires_a_session(client: AsyncClient):
    response = await client.post(
        "/v1/billing/purchases/verify",
        json={"platform": "ios", "receipt": "scangenai_pro_monthly:tx-1"},
    )
    assert response.status_code == 401
