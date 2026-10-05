"""Mobile log ingestion: authenticated, bounded and re-sanitised."""

import logging

import pytest
from httpx import AsyncClient

from app.services.client_logs import sanitize_context
from app.utils.logger import logger

RECORD = {
    "timestamp": "2026-10-05T12:00:00Z",
    "level": "error",
    "scope": "api",
    "event": "request_failed",
    "message": "Upload failed",
    "runtime_id": "rt-123",
    "context": {
        "status": 500,
        "token": "eyJhbGciOiJIUzI1NiJ9.e30.sig",
        "path": "/v1/me",
    },
}


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


@pytest.mark.asyncio
async def test_client_logs_require_a_session(client: AsyncClient):
    response = await client.post("/v1/client-logs", json={"records": [RECORD]})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_client_logs_land_in_the_server_log_redacted(
    client: AsyncClient, authenticated_user: dict
):
    capture = _Capture()
    logger.addHandler(capture)
    try:
        response = await client.post(
            "/v1/client-logs",
            json={"records": [RECORD]},
            headers=authenticated_user["headers"],
        )
    finally:
        logger.removeHandler(capture)

    assert response.status_code == 202
    assert response.json() == {"accepted": 1}
    line = next(l for l in capture.lines if l.startswith("mobile.api.request_failed"))
    assert "Upload failed" in line
    assert "eyJhbGci" not in line
    assert '"token":"[REDACTED]"' in line


@pytest.mark.asyncio
async def test_oversized_or_malformed_batches_are_refused(
    client: AsyncClient, authenticated_user: dict
):
    headers = authenticated_user["headers"]
    too_many = await client.post(
        "/v1/client-logs", json={"records": [RECORD] * 51}, headers=headers
    )
    bad_scope = await client.post(
        "/v1/client-logs",
        json={"records": [{**RECORD, "scope": "a b\nINJECTED"}]},
        headers=headers,
    )
    huge_context = await client.post(
        "/v1/client-logs",
        json={"records": [{**RECORD, "context": {"blob": "x" * 5000}}]},
        headers=headers,
    )
    assert too_many.status_code == 422
    assert bad_scope.status_code == 422
    assert huge_context.status_code == 422


def test_sanitize_context_bounds_depth_strips_control_characters_and_redacts():
    cleaned = sanitize_context(
        {
            "password": "hunter2",
            "note": "line one\nFAKE LOG LINE",
            "nested": {"a": {"b": {"c": {"d": "too deep"}}}},
            "items": list(range(100)),
        }
    )
    assert cleaned["password"] == "[REDACTED]"
    assert "\n" not in cleaned["note"]
    assert cleaned["nested"]["a"]["b"]["c"] == "[Truncated]"
    assert len(cleaned["items"]) == 25
