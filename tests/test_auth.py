"""Tests for authentication routes."""

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
@patch("app.routes.auth.send_verification_email")
async def test_register_success(
    mock_send_email, client: AsyncClient, test_user_data: dict
):
    """Test successful user registration."""
    mock_send_email.return_value = None
    response = await client.post("/auth/register", json=test_user_data)
    assert response.status_code == 201
    data = response.json()
    assert "message" in data
    assert "registered successfully" in data["message"].lower()


@pytest.mark.asyncio
@patch("app.routes.auth.send_verification_email")
async def test_register_duplicate_email(
    mock_send_email, client: AsyncClient, test_user_data: dict, registered_user
):
    """Duplicate registration does not disclose whether the account exists."""
    mock_send_email.return_value = None
    response = await client.post("/auth/register", json=test_user_data)
    assert response.status_code == 201
    data = response.json()
    assert "message" in data
    assert "already" not in data["message"].lower()


@pytest.mark.asyncio
async def test_login_success(
    client: AsyncClient, registered_user, test_user_data: dict
):
    """Test successful login."""
    response = await client.post(
        "/auth/login",
        json={"email": test_user_data["email"], "password": test_user_data["password"]},
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"
    assert "name" in data
    assert data["name"] == test_user_data["name"]
    assert "user_id" in data
    assert data["user_id"] == str(registered_user.id)


@pytest.mark.asyncio
async def test_login_invalid_password(
    client: AsyncClient, registered_user, test_user_data: dict
):
    """Test login with incorrect password."""
    response = await client.post(
        "/auth/login",
        json={"email": test_user_data["email"], "password": "wrongpassword"},
    )
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_refresh_token_success(client: AsyncClient, authenticated_user: dict):
    """Test successful token refresh."""
    response = await client.post(
        "/auth/refresh", json={"refresh_token": authenticated_user["refresh_token"]}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["refresh_token"] != authenticated_user["refresh_token"]


@pytest.mark.asyncio
async def test_refresh_token_reuse_revokes_family(
    client: AsyncClient, authenticated_user: dict
):
    """A used refresh token cannot be replayed and poisons its token family."""
    original = authenticated_user["refresh_token"]
    first = await client.post("/auth/refresh", json={"refresh_token": original})
    assert first.status_code == 200
    rotated = first.json()["refresh_token"]

    replay = await client.post("/auth/refresh", json={"refresh_token": original})
    assert replay.status_code == 401

    family_member = await client.post("/auth/refresh", json={"refresh_token": rotated})
    assert family_member.status_code == 401


@pytest.mark.asyncio
async def test_multibyte_password_over_bcrypt_limit_is_rejected(
    client: AsyncClient,
):
    """Passwords are rejected by UTF-8 byte length, never silently truncated."""
    response = await client.post(
        "/auth/register",
        json={
            "name": "Byte Limit",
            "email": "bytes@example.com",
            "password": "é" * 40,
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_logout_success(client: AsyncClient, authenticated_user: dict):
    """Test successful logout."""
    response = await client.post(
        "/auth/logout",
        json={"refresh_token": authenticated_user["refresh_token"]},
        headers=authenticated_user["headers"],
    )
    assert response.status_code == 200
    data = response.json()
    assert "message" in data


@pytest.mark.asyncio
@patch("app.routes.auth.delete_user_pdf_data", new_callable=AsyncMock)
async def test_delete_account_erases_user_and_invalidates_token(
    mock_delete_pdf_data,
    client: AsyncClient,
    authenticated_user: dict,
):
    """Deletion removes the identity, server sessions, and user-owned RAG data."""
    response = await client.request(
        "DELETE",
        "/auth/account",
        json={"password": "testpassword123"},
        headers=authenticated_user["headers"],
    )
    assert response.status_code == 200
    mock_delete_pdf_data.assert_awaited_once()

    retry = await client.post(
        "/auth/logout",
        json={"refresh_token": authenticated_user["refresh_token"]},
        headers=authenticated_user["headers"],
    )
    assert retry.status_code == 401


@pytest.mark.asyncio
@patch("app.routes.auth.delete_user_pdf_data", new_callable=AsyncMock)
async def test_delete_account_requires_current_password(
    mock_delete_pdf_data,
    client: AsyncClient,
    authenticated_user: dict,
):
    response = await client.request(
        "DELETE",
        "/auth/account",
        json={"password": "wrongpassword"},
        headers=authenticated_user["headers"],
    )
    assert response.status_code == 401
    mock_delete_pdf_data.assert_not_awaited()


@pytest.mark.asyncio
async def test_access_protected_route_without_auth(client: AsyncClient):
    """Test accessing protected route without authentication."""
    response = await client.post("/convert/image/text")
    assert response.status_code == 403
