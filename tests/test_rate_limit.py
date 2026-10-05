"""The PostgreSQL rate limiter, and which client a request is counted against."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request

from app.config import override_settings
from app.utils.rate_limit import (
    RateLimiter,
    _client_identity,
    get_rate_limit_key,
    parse_limit,
)
from tests.conftest import TestSessionLocal, requires_postgres


def _request(forwarded: str | None = None, peer: str = "10.0.0.9") -> Request:
    headers = [(b"x-forwarded-for", forwarded.encode())] if forwarded else []
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/v1/auth/login",
            "headers": headers,
            "query_string": b"",
            "client": (peer, 1234),
        }
    )


def test_parse_limit():
    assert parse_limit("5/minute") == (5, 60)
    assert parse_limit(" 120 / hour ") == (120, 3600)
    for bad in ("0/minute", "5/fortnight", "lots"):
        with pytest.raises(ValueError):
            parse_limit(bad)


def test_forwarded_header_is_ignored_unless_the_proxy_is_trusted():
    request = _request("203.0.113.7")
    with override_settings(trust_proxy_headers=False):
        assert get_rate_limit_key(request) == "10.0.0.9"


def test_only_the_last_forwarded_entry_is_trusted():
    """The first entry is whatever the client sent; the proxy appends the last."""
    request = _request("1.1.1.1, 2.2.2.2, 198.51.100.4")
    with override_settings(trust_proxy_headers=True):
        assert get_rate_limit_key(request) == "198.51.100.4"


def test_authenticated_endpoints_are_counted_per_user_not_per_ip():
    request = _request()
    user = SimpleNamespace(id="user-1")
    assert _client_identity(request, {"current_user": user}) == "user:user-1"
    assert _client_identity(request, {}) == "ip:10.0.0.9"


@requires_postgres
@pytest.mark.asyncio
async def test_limiter_counts_atomically_and_hashes_its_keys(db_session: AsyncSession):
    limiter = RateLimiter(TestSessionLocal)

    for _ in range(3):
        await limiter.hit("scope", "ip:203.0.113.7", 3, 60)
    with pytest.raises(HTTPException) as raised:
        await limiter.hit("scope", "ip:203.0.113.7", 3, 60)

    assert raised.value.status_code == 429
    assert int(raised.value.headers["Retry-After"]) >= 1
    # Another client, and another scope, have their own budget.
    await limiter.hit("scope", "ip:203.0.113.8", 3, 60)
    await limiter.hit("other", "ip:203.0.113.7", 3, 60)

    keys = (
        (await db_session.execute(text("SELECT key FROM rate_limit_buckets")))
        .scalars()
        .all()
    )
    assert len(keys) == 3
    assert all(len(key) == 64 and "203.0.113" not in key for key in keys)


@requires_postgres
@pytest.mark.asyncio
async def test_limiter_fails_closed_when_it_cannot_count(db_session: AsyncSession):
    await db_session.execute(text("DROP TABLE rate_limit_buckets"))
    await db_session.commit()

    with pytest.raises(HTTPException) as raised:
        await RateLimiter(TestSessionLocal).hit("scope", "ip:1.2.3.4", 3, 60)

    assert raised.value.status_code == 503


@requires_postgres
@pytest.mark.asyncio
async def test_login_is_rate_limited_end_to_end(client: AsyncClient):
    body = {"email": "nobody@example.com", "password": "wrong-password"}
    statuses = [
        (await client.post("/v1/auth/login", json=body)).status_code for _ in range(21)
    ]
    assert statuses[:20] == [401] * 20
    assert statuses[20] == 429
