"""Sign in with Google and Apple: verified here, keyed by subject, never email."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import User
from app.services.apple_auth import AppleClaims
from app.utils import get_password_hash

GOOGLE = {
    "sub": "google-subject-1",
    "email": "Ada@Example.com",
    "email_verified": True,
    "name": "Ada Lovelace",
}


def _google(claims=None, error=None):
    return patch(
        "app.routes.auth.verify_google_id_token",
        side_effect=error,
        return_value=claims or GOOGLE,
    )


def _apple(sub="apple-subject-1", email="ada@example.com", verified=True):
    return patch(
        "app.routes.auth.verify_apple_identity_token",
        new_callable=AsyncMock,
        return_value=AppleClaims(sub, email, verified, False),
    )


async def _user(db: AsyncSession, email: str) -> User:
    return (await db.execute(select(User).where(User.email == email))).scalar_one()


@pytest.mark.asyncio
async def test_google_creates_a_verified_passwordless_account(
    client: AsyncClient, db_session: AsyncSession
):
    with _google():
        response = await client.post("/v1/auth/google", json={"id_token": "t"})

    assert response.status_code == 200
    assert response.json()["name"] == "Ada Lovelace"
    user = await _user(db_session, "ada@example.com")
    assert (user.google_sub, user.is_verified, user.hashed_password) == (
        "google-subject-1",
        True,
        None,
    )
    headers = {"Authorization": f"Bearer {response.json()['access_token']}"}
    me = (await client.get("/v1/me", headers=headers)).json()
    assert me["login_methods"] == ["google"]


@pytest.mark.asyncio
async def test_the_subject_not_the_email_identifies_a_returning_user(
    client: AsyncClient, db_session: AsyncSession
):
    with _google():
        first = await client.post("/v1/auth/google", json={"id_token": "t"})
    # The same Google account, after its owner changed their address.
    with _google({**GOOGLE, "email": "new@example.com"}):
        second = await client.post("/v1/auth/google", json={"id_token": "t"})

    assert first.json()["user_id"] == second.json()["user_id"]
    users = (await db_session.execute(select(User))).scalars().all()
    assert len(users) == 1


@pytest.mark.asyncio
async def test_an_invalid_or_unverified_token_is_refused(
    client: AsyncClient, db_session: AsyncSession
):
    with _google(error=ValueError("bad signature")):
        invalid = await client.post("/v1/auth/google", json={"id_token": "t"})
    with _google({**GOOGLE, "email_verified": False}):
        unverified = await client.post("/v1/auth/google", json={"id_token": "t"})

    assert invalid.status_code == 401
    assert unverified.status_code == 400
    assert (await db_session.execute(select(User))).scalars().all() == []


@pytest.mark.asyncio
async def test_linking_to_a_verified_password_account_keeps_the_password(
    client: AsyncClient, registered_user: User, db_session: AsyncSession
):
    with _apple(email=registered_user.email):
        response = await client.post("/v1/auth/apple", json={"identity_token": "t"})

    assert response.status_code == 200
    assert response.json()["user_id"] == str(registered_user.id)
    await db_session.refresh(registered_user)
    assert registered_user.apple_sub == "apple-subject-1"
    assert registered_user.hashed_password is not None
    login = await client.post(
        "/v1/auth/login",
        json={"email": registered_user.email, "password": "testpassword123"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_linking_to_an_unverified_account_discards_its_unproven_password(
    client: AsyncClient, db_session: AsyncSession
):
    """Otherwise anyone could pre-register a victim's address with a password
    of their own and keep access after the victim signs in with Google."""
    db_session.add(
        User(
            name="Squatter",
            email="ada@example.com",
            hashed_password=get_password_hash("attacker-password"),
            is_verified=False,
        )
    )
    await db_session.commit()

    with _google():
        response = await client.post("/v1/auth/google", json={"id_token": "t"})
    attacker = await client.post(
        "/v1/auth/login",
        json={"email": "ada@example.com", "password": "attacker-password"},
    )

    assert response.status_code == 200
    assert attacker.status_code == 401
    user = await _user(db_session, "ada@example.com")
    await db_session.refresh(user)
    assert (user.is_verified, user.hashed_password) == (True, None)


@pytest.mark.asyncio
async def test_apple_uses_the_name_the_app_passes_once(
    client: AsyncClient, db_session: AsyncSession
):
    with _apple():
        response = await client.post(
            "/v1/auth/apple", json={"identity_token": "t", "name": " Ada L "}
        )
    assert response.json()["name"] == "Ada L"


@pytest.mark.asyncio
@patch("app.routes.auth.mark_account_deleted_and_purge_jobs")
@patch("app.routes.auth.delete_user_pdf_data", new_callable=AsyncMock)
async def test_a_passwordless_account_is_deleted_with_a_fresh_provider_token(
    _delete_pdf, _purge, client: AsyncClient, db_session: AsyncSession
):
    with _google():
        session = (await client.post("/v1/auth/google", json={"id_token": "t"})).json()
    headers = {"Authorization": f"Bearer {session['access_token']}"}

    # A bearer token alone is not proof, and neither is someone else's identity.
    nothing = await client.request(
        "DELETE", "/v1/auth/account", json={}, headers=headers
    )
    with _google({**GOOGLE, "sub": "someone-else"}):
        stranger = await client.request(
            "DELETE", "/v1/auth/account", json={"google_id_token": "t"}, headers=headers
        )
    with _google():
        owner = await client.request(
            "DELETE", "/v1/auth/account", json={"google_id_token": "t"}, headers=headers
        )

    assert nothing.status_code == 401
    assert stranger.status_code == 401
    assert owner.status_code == 200
    assert (await db_session.execute(select(User))).scalars().all() == []


@pytest.mark.asyncio
async def test_a_passwordless_account_cannot_sign_in_with_any_password(
    client: AsyncClient,
):
    with _google():
        await client.post("/v1/auth/google", json={"id_token": "t"})
    response = await client.post(
        "/v1/auth/login", json={"email": "ada@example.com", "password": "anything-1"}
    )
    assert response.status_code == 401
