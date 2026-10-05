"""The read-only admin console: off by default, locked after failures."""

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings, override_settings
from app.main import app as real_app
from app.routes.admin import build_admin_router
from tests.conftest import TestSessionLocal, requires_postgres

TOKEN = "t" * 40
PATH = "/ops-0123456789abcdef"


def test_the_console_is_not_registered_without_a_token():
    assert not get_settings().admin_enabled
    assert not any("/admin" in getattr(r, "path", "") for r in real_app.routes)


async def _console() -> AsyncClient:
    with override_settings(
        admin_dashboard_token=TOKEN,
        admin_dashboard_path=PATH,
        admin_dashboard_username="operator",
        admin_lock_threshold=3,
    ) as settings:
        app = FastAPI()
        app.include_router(build_admin_router(settings, TestSessionLocal))
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@requires_postgres
@pytest.mark.asyncio
async def test_sign_in_view_and_sign_out(db_session: AsyncSession):
    async with await _console() as client:
        anonymous = await client.get(PATH)
        assert anonymous.status_code == 200
        assert "Sign in" in anonymous.text and "Users" not in anonymous.text

        denied = await client.post(
            f"{PATH}/login", data={"username": "operator", "token": "wrong"}
        )
        assert denied.status_code == 401

        accepted = await client.post(
            f"{PATH}/login", data={"username": "operator", "token": TOKEN}
        )
        assert accepted.status_code == 303
        cookie = accepted.headers["set-cookie"].lower()
        assert "httponly" in cookie and "samesite=strict" in cookie

        console = await client.get(PATH)
        assert console.status_code == 200
        assert "Verified users" in console.text
        assert "critical: backups" in console.text
        assert console.headers["cache-control"] == "no-store"
        assert "default-src 'none'" in console.headers["content-security-policy"]

        await client.post(f"{PATH}/logout")
        client.cookies.clear()
        assert "Sign in" in (await client.get(PATH)).text


@requires_postgres
@pytest.mark.asyncio
async def test_the_console_locks_in_postgres_and_refuses_even_the_right_token(
    db_session: AsyncSession,
):
    async with await _console() as client:
        for _ in range(3):
            await client.post(
                f"{PATH}/login", data={"username": "operator", "token": "wrong"}
            )
        locked = await client.post(
            f"{PATH}/login", data={"username": "operator", "token": TOKEN}
        )
        assert locked.status_code == 423

        # `make unlock-admin` runs exactly this statement.
        await db_session.execute(
            text(
                "UPDATE admin_security SET failed_attempts = 0, locked_until = NULL "
                "WHERE id = 1"
            )
        )
        await db_session.commit()
        reopened = await client.post(
            f"{PATH}/login", data={"username": "operator", "token": TOKEN}
        )
        assert reopened.status_code == 303
