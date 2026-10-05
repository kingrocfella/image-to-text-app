"""Resend verification, expiring links, and password reset."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import RefreshSession, User
from app.utils import get_password_hash, token_fingerprint

PASSWORD = "password123"


async def _user(db: AsyncSession, *, verified: bool, **extra) -> User:
    user = User(
        name="Ada",
        email="ada@example.com",
        hashed_password=get_password_hash(PASSWORD),
        is_verified=verified,
        **extra,
    )
    db.add(user)
    await db.commit()
    return user


@pytest.mark.asyncio
async def test_unverified_login_with_the_right_password_says_so(
    client: AsyncClient, db_session: AsyncSession
):
    await _user(db_session, verified=False)

    wrong = await client.post(
        "/v1/auth/login", json={"email": "ada@example.com", "password": "nope-nope"}
    )
    right = await client.post(
        "/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )

    # Only a caller who knows the password learns the account is unverified.
    assert wrong.status_code == 401
    assert right.status_code == 403
    assert right.json()["detail"] == "Email not verified"


@pytest.mark.asyncio
@patch("app.routes.auth.send_verification_email")
async def test_resend_verification_replaces_the_token(
    mock_send, client: AsyncClient, db_session: AsyncSession
):
    user = await _user(
        db_session, verified=False, verification_token=token_fingerprint("old")
    )

    response = await client.post(
        "/v1/auth/resend-verification", json={"email": "ada@example.com"}
    )

    assert response.status_code == 200
    mock_send.assert_called_once()
    new_token = mock_send.call_args.args[1]
    await db_session.refresh(user)
    assert user.verification_token == token_fingerprint(new_token)
    assert user.verification_expires_at is not None
    old = await client.get("/auth/verify-email", params={"token": "old"})
    assert old.status_code == 400


@pytest.mark.asyncio
@patch("app.routes.auth.send_verification_email")
async def test_resend_verification_does_not_reveal_accounts(
    mock_send, client: AsyncClient, db_session: AsyncSession
):
    await _user(db_session, verified=True)

    known = await client.post(
        "/v1/auth/resend-verification", json={"email": "ada@example.com"}
    )
    unknown = await client.post(
        "/v1/auth/resend-verification", json={"email": "nobody@example.com"}
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    mock_send.assert_not_called()


@pytest.mark.asyncio
async def test_expired_verification_link_is_refused(
    client: AsyncClient, db_session: AsyncSession
):
    user = await _user(
        db_session,
        verified=False,
        verification_token=token_fingerprint("tok"),
        verification_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    response = await client.get("/auth/verify-email", params={"token": "tok"})

    assert response.status_code == 400
    assert "expired" in response.text.lower()
    await db_session.refresh(user)
    assert user.is_verified is False


@pytest.mark.asyncio
@patch("app.routes.auth.send_password_reset_email")
async def test_forgot_password_stores_only_a_hash_and_reveals_nothing(
    mock_send, client: AsyncClient, db_session: AsyncSession
):
    user = await _user(db_session, verified=True)

    known = await client.post(
        "/v1/auth/forgot-password", json={"email": "ada@example.com"}
    )
    unknown = await client.post(
        "/v1/auth/forgot-password", json={"email": "nobody@example.com"}
    )

    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json()
    mock_send.assert_called_once()
    token = mock_send.call_args.args[1]
    await db_session.refresh(user)
    assert user.password_reset_token == token_fingerprint(token)
    assert token not in str(user.password_reset_token)


@pytest.mark.asyncio
async def test_reset_page_rejects_unknown_and_expired_tokens(
    client: AsyncClient, db_session: AsyncSession
):
    await _user(
        db_session,
        verified=True,
        password_reset_token=token_fingerprint("stale"),
        password_reset_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
    )

    for token in ("unknown", "stale"):
        response = await client.get("/auth/reset-password", params={"token": token})
        assert response.status_code == 400
    missing = await client.get("/auth/reset-password")
    assert missing.status_code == 400


@pytest.mark.asyncio
@patch("app.routes.auth.send_password_reset_email")
async def test_password_reset_end_to_end(
    mock_send, client: AsyncClient, db_session: AsyncSession
):
    user = await _user(db_session, verified=True)
    login = await client.post(
        "/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    old_tokens = login.json()
    # Make the existing access token unambiguously older than the reset.
    user.password_changed_at = None
    await client.post("/v1/auth/forgot-password", json={"email": "ada@example.com"})
    token = mock_send.call_args.args[1]

    page = await client.get("/auth/reset-password", params={"token": token})
    assert page.status_code == 200
    assert "form-action 'self'" in page.headers["content-security-policy"]

    mismatch = await client.post(
        "/auth/reset-password/form",
        data={"token": token, "password": "new-password-1", "confirm_password": "x"},
    )
    assert mismatch.status_code == 400
    short = await client.post(
        "/auth/reset-password/form",
        data={"token": token, "password": "short", "confirm_password": "short"},
    )
    assert short.status_code == 400

    done = await client.post(
        "/auth/reset-password/form",
        data={
            "token": token,
            "password": "new-password-1",
            "confirm_password": "new-password-1",
        },
    )
    assert done.status_code == 200

    # The link works once.
    again = await client.get("/auth/reset-password", params={"token": token})
    assert again.status_code == 400

    old_login = await client.post(
        "/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    new_login = await client.post(
        "/v1/auth/login",
        json={"email": "ada@example.com", "password": "new-password-1"},
    )
    assert old_login.status_code == 401
    assert new_login.status_code == 200

    # Every session from before the reset is dead.
    refresh = await client.post(
        "/v1/auth/refresh", json={"refresh_token": old_tokens["refresh_token"]}
    )
    assert refresh.status_code == 401
    sessions = (
        (
            await db_session.execute(
                select(RefreshSession).where(RefreshSession.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert any(s.revoked_at is not None for s in sessions)


@pytest.mark.asyncio
async def test_access_token_from_before_a_password_change_is_rejected(
    client: AsyncClient, db_session: AsyncSession
):
    user = await _user(db_session, verified=True)
    login = await client.post(
        "/v1/auth/login", json={"email": "ada@example.com", "password": PASSWORD}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    assert (await client.get("/v1/me", headers=headers)).status_code == 200

    user.password_changed_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    await db_session.commit()

    assert (await client.get("/v1/me", headers=headers)).status_code == 401


@pytest.mark.asyncio
async def test_reset_also_verifies_the_account(
    client: AsyncClient, db_session: AsyncSession
):
    user = await _user(
        db_session,
        verified=False,
        password_reset_token=token_fingerprint("tok"),
        password_reset_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )

    done = await client.post(
        "/auth/reset-password/form",
        data={
            "token": "tok",
            "password": "new-password-1",
            "confirm_password": "new-password-1",
        },
    )

    assert done.status_code == 200
    await db_session.refresh(user)
    assert user.is_verified is True
