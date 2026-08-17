"""Regression tests for the P1 request, token, and upload boundaries."""

import asyncio
from io import BytesIO
from types import SimpleNamespace
import uuid

import jwt
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from httpx import ASGITransport, AsyncClient
from PIL import Image

from app.middleware.security_middleware import (
    DistributedRateLimitMiddleware,
    RequestProtectionMiddleware,
)
from app.utils import (
    create_access_token,
    read_upload_limited,
    validate_image_content,
)
from app.utils.auth_utils import JWT_AUDIENCE, JWT_ISSUER, SECRET_KEY
from app.utils import rag_vectorstore
from app.utils.logger import sanitize_log_text


@pytest.mark.asyncio
async def test_global_body_limit_and_security_headers():
    protected = FastAPI()
    protected.add_middleware(
        RequestProtectionMiddleware,
        max_body_bytes=8,
        timeout_seconds=1,
        enable_hsts=True,
    )

    @protected.post("/echo")
    async def echo(request: Request):
        return {"size": len(await request.body())}

    async with AsyncClient(
        transport=ASGITransport(app=protected), base_url="https://test"
    ) as client:
        accepted = await client.post("/echo", content=b"12345678")
        rejected = await client.post("/echo", content=b"123456789")

    assert accepted.status_code == 200
    assert accepted.headers["x-content-type-options"] == "nosniff"
    assert accepted.headers["strict-transport-security"].startswith("max-age=")
    assert rejected.status_code == 413


@pytest.mark.asyncio
async def test_global_request_deadline():
    protected = FastAPI()
    protected.add_middleware(
        RequestProtectionMiddleware,
        max_body_bytes=1024,
        timeout_seconds=0.01,
        enable_hsts=False,
    )

    @protected.get("/slow")
    async def slow():
        await asyncio.sleep(0.1)
        return {"ok": True}

    async with AsyncClient(
        transport=ASGITransport(app=protected), base_url="http://test"
    ) as client:
        response = await client.get("/slow")

    assert response.status_code == 504


@pytest.mark.asyncio
async def test_request_protection_does_not_spin_on_streaming_response():
    protected = FastAPI()
    protected.add_middleware(
        RequestProtectionMiddleware,
        max_body_bytes=1024,
        timeout_seconds=1,
        enable_hsts=False,
    )

    @protected.get("/stream")
    async def stream():
        async def chunks():
            yield b"ready"

        return StreamingResponse(chunks())

    async with AsyncClient(
        transport=ASGITransport(app=protected), base_url="http://test"
    ) as client:
        response = await client.get("/stream")

    assert response.status_code == 200
    assert response.content == b"ready"


@pytest.mark.asyncio
async def test_distributed_rate_limit_returns_429(monkeypatch):
    limited = FastAPI()
    limited.add_middleware(DistributedRateLimitMiddleware)

    @limited.post("/auth/login")
    async def login_stub():
        return {"ok": True}

    monkeypatch.setattr(
        DistributedRateLimitMiddleware,
        "_increment",
        lambda _self, _key, _window: 11,
    )
    async with AsyncClient(
        transport=ASGITransport(app=limited), base_url="http://test"
    ) as client:
        response = await client.post("/auth/login")

    assert response.status_code == 429
    assert response.headers["retry-after"] == "900"


def test_access_tokens_have_bound_identity_claims():
    token = create_access_token({"sub": "00000000-0000-0000-0000-000000000001"})
    claims = jwt.decode(
        token,
        SECRET_KEY,
        algorithms=["HS256"],
        issuer=JWT_ISSUER,
        audience=JWT_AUDIENCE,
    )
    assert claims["iss"] == JWT_ISSUER
    assert claims["aud"] == JWT_AUDIENCE
    assert claims["jti"]
    assert claims["type"] == "access"


@pytest.mark.asyncio
async def test_upload_reader_stops_at_configured_limit():
    from fastapi import UploadFile

    upload = UploadFile(filename="oversized.bin", file=BytesIO(b"x" * 9))
    with pytest.raises(Exception) as exc_info:
        await read_upload_limited(upload, 8)
    assert getattr(exc_info.value, "status_code", None) == 413


def test_image_content_validation_rejects_masquerading_bytes():
    with pytest.raises(Exception) as exc_info:
        validate_image_content(b"not an image")
    assert getattr(exc_info.value, "status_code", None) == 400

    output = BytesIO()
    Image.new("RGB", (4, 4), color="red").save(output, format="PNG")
    validate_image_content(output.getvalue())


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _RecordingSession:
    def __init__(self, result_values):
        self.result_values = result_values
        self.statements = []
        self.commits = 0

    async def execute(self, statement):
        self.statements.append(statement)
        values, self.result_values = self.result_values, []
        return _ScalarResult(values)

    async def commit(self):
        self.commits += 1


@pytest.mark.asyncio
async def test_expired_pdf_retention_removes_vectors_before_metadata(monkeypatch):
    expired = SimpleNamespace(id=uuid.uuid4(), collection_name="expired-collection")
    session = _RecordingSession([expired])
    removed = []

    async def record_delete(names):
        removed.extend(names)

    monkeypatch.setattr(rag_vectorstore, "delete_vector_collections", record_delete)
    count = await rag_vectorstore.purge_expired_pdf_data(session)

    assert count == 1
    assert removed == ["expired-collection"]
    assert len(session.statements) == 2
    assert session.commits == 1


def test_log_sink_redacts_credentials_and_email_addresses():
    rendered = sanitize_log_text(
        "user@example.com Bearer abc.def.ghi https://app/verify?token=secret "
        "/app/shared_files/private-invoice.pdf"
    )
    assert "user@example.com" not in rendered
    assert "abc.def.ghi" not in rendered
    assert "token=secret" not in rendered
    assert "private-invoice.pdf" not in rendered
