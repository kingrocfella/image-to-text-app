"""Tests for verification emails and the verification result page."""

from unittest.mock import MagicMock, patch

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import User
from app.utils import get_password_hash, token_fingerprint
from app.utils.email_utils import render_template, send_verification_email


async def _unverified_user(db_session: AsyncSession, token: str) -> User:
    user = User(
        name="Ada",
        email="ada@example.com",
        hashed_password=get_password_hash("password123"),
        is_verified=False,
        verification_token=token_fingerprint(token),
    )
    db_session.add(user)
    await db_session.commit()
    return user


@pytest.mark.asyncio
async def test_verify_email_success_renders_page(
    client: AsyncClient, db_session: AsyncSession
):
    user = await _unverified_user(db_session, "good-token")

    response = await client.get("/auth/verify-email", params={"token": "good-token"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "Your email is verified" in response.text
    await db_session.refresh(user)
    assert user.is_verified is True
    assert user.verification_token is None


@pytest.mark.asyncio
async def test_verify_email_reused_link_is_invalid(
    client: AsyncClient, db_session: AsyncSession
):
    await _unverified_user(db_session, "once-token")
    await client.get("/auth/verify-email", params={"token": "once-token"})

    response = await client.get("/auth/verify-email", params={"token": "once-token"})

    assert response.status_code == 400
    assert "couldn’t verify your email" in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("params", [{"token": "nope"}, {}])
async def test_verify_email_invalid_or_missing_token(client: AsyncClient, params):
    response = await client.get("/auth/verify-email", params=params)

    assert response.status_code == 400
    assert response.headers["content-type"].startswith("text/html")
    assert "Link not valid" in response.text


@pytest.mark.asyncio
async def test_verify_email_page_csp_allows_only_its_nonced_styles(
    client: AsyncClient,
):
    response = await client.get("/auth/verify-email", params={"token": "nope"})

    csp = response.headers["content-security-policy"]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert "default-src 'none'" in csp
    assert "script-src" not in csp
    assert f'<style nonce="{nonce}">' in response.text


def test_verification_email_escapes_user_name():
    html = render_template(
        "emails/verify_email.html",
        name="<script>alert(1)</script>",
        verification_url="https://api.example.com/auth/verify-email?token=abc",
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'href="https://api.example.com/auth/verify-email?token=abc"' in html


@patch("app.utils.email_utils.smtplib.SMTP")
def test_send_verification_email_is_multipart(mock_smtp, monkeypatch):
    for key, value in {
        "SMTP_SERVER": "smtp.example.com",
        "SMTP_PORT": "587",
        "SMTP_USERNAME": "no-reply@example.com",
        "SMTP_PASSWORD": "pw",
        "APP_URL": "https://api.example.com/",
    }.items():
        monkeypatch.setenv(key, value)
    server = MagicMock()
    mock_smtp.return_value.__enter__.return_value = server

    send_verification_email("ada@example.com", "tok123", name="Ada")

    msg = server.send_message.call_args.args[0]
    assert msg["To"] == "ada@example.com"
    assert "no-reply@example.com" in msg["From"]
    text = msg.get_body(preferencelist=("plain",)).get_content()
    html = msg.get_body(preferencelist=("html",)).get_content()
    url = "https://api.example.com/auth/verify-email?token=tok123"
    assert url in text
    assert url in html
    assert "Hi Ada" in html
