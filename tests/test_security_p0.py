"""Regression tests for P0 security controls."""

from unittest.mock import patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.main import not_found_handler
from app.queues.job_status import JobNotFoundError, get_job_status


def test_required_jwt_secret_fails_closed():
    """JWT signing must not fall back to a built-in or placeholder secret."""
    from app.config import ConfigError, load_settings

    base = {
        "POSTGRES_USER": "u",
        "POSTGRES_PASSWORD": "p",
        "POSTGRES_DB": "d",
        "POSTGRES_HOST": "h",
    }
    for secret in ("", "too-short", "your-secret-key-that-is-long-but-placeholder"):
        with pytest.raises(ConfigError):
            load_settings({**base, "SECRET_KEY": secret})


def test_404_is_bounded_first_party_json():
    """Unknown paths must not redirect users to an external large download."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/missing",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
        }
    )

    response = not_found_handler(request, HTTPException(status_code=404))

    assert response.status_code == 404
    assert response.headers.get("location") is None
    assert response.body == b'{"detail":"Not found"}'


@patch("app.queues.job_status._get_job_metadata")
def test_job_status_rejects_a_different_owner(mock_metadata):
    """Knowing another user's message ID must never reveal their result."""
    mock_metadata.return_value = {
        "job_type": "rag",
        "owner_user_id": "owner-user-id",
    }

    with pytest.raises(JobNotFoundError):
        get_job_status("known-message-id", "attacker-user-id")
