"""Pytest configuration and fixtures."""

# pylint: disable=import-error,redefined-outer-name,unused-argument,unexpected-keyword-arg,no-member

import os
import sys
from importlib import import_module
from io import BytesIO
from typing import AsyncGenerator
from unittest.mock import MagicMock

# The suite must not depend on, or be changed by, a developer's real .env:
# these are set before app.config loads, and real process variables win over
# the file. TEST_DATABASE_URL (a throwaway PostgreSQL database) is the one
# value taken from the environment.
os.environ.update(
    {
        "ENVIRONMENT": "dev",
        "APP_URL": "http://test",
        "SECRET_KEY": "test-secret-key-for-testing-only-32-bytes",
        "SECRET_KEY_PREVIOUS": "",
        "POSTGRES_USER": "test",
        "POSTGRES_PASSWORD": "test",
        "POSTGRES_DB": "test",
        "POSTGRES_HOST": "127.0.0.1",
        "POSTGRES_PORT": "5432",
        "LOG_TO_FILE": "false",
        "MINIMUM_APP_VERSION": "0.0.0",
        "TRUST_PROXY_HEADERS": "false",
        "OPENAI_API_KEY": "test-openai-key",
        "GEMINI_API_KEY": "test-gemini-key",
        "DEEPSEEK_API_KEY": "",
        "ANTHROPIC_API_KEY": "test-anthropic-key",
        "FREE_CLOUD_MODELS": "gemini,deepseek",
        "PRO_QUOTA_IMAGE_MONTHLY": "1000",
        "PRO_QUOTA_SOUND_MONTHLY": "300",
        "PRO_QUOTA_PDF_MONTHLY": "500",
        "PRO_QUOTA_CLOUD_MODEL_MONTHLY": "300",
        "BILLING_PROVIDER": "dev",
        "GOOGLE_WEB_CLIENT_ID": "test-web-client.apps.googleusercontent.com",
        "IOS_BUNDLE_ID": "com.leonfrontier.scangenai",
        "ANDROID_PACKAGE_NAME": "com.leonfrontier.scangenai",
        "QUOTA_IMAGE_MONTHLY": "300",
        "QUOTA_SOUND_MONTHLY": "100",
        "QUOTA_PDF_MONTHLY": "200",
        "QUOTA_CLOUD_MODEL_MONTHLY": "50",
        "SMTP_SERVER": "",
        "SMTP_USERNAME": "",
        "SMTP_PASSWORD": "",
        "NOTIFY_EMAILS_ENABLED": "false",
        "ADMIN_DASHBOARD_TOKEN": "",
    }
)

# The OCR / speech / RAG stack is several gigabytes. Nothing in this suite runs
# a model, so where it is not installed the modules are replaced by mocks and
# the API layer can still be imported and tested.
for _heavy in (
    "paddleocr",
    "librosa",
    "torch",
    "transformers",
    "langchain_community",
    "langchain_community.document_loaders",
    "langchain_openai",
    "langchain_qdrant",
    "langchain_text_splitters",
):
    try:
        import_module(_heavy)
    except Exception:  # pylint: disable=broad-exception-caught
        sys.modules[_heavy] = MagicMock()

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import Base, User, get_db
from app.database.postgres import init_db
from app.main import app as test_app
from app.utils import get_password_hash
from app.utils.rate_limit import limiter

# Configure pytest-asyncio
pytest_plugins = ("pytest_asyncio",)

# With TEST_DATABASE_URL the suite runs against real PostgreSQL, built exactly
# as startup builds it (model tables, then every migration), and the rate
# limiter, admin console and operations tests run too. Without it everything
# that can run on SQLite does, and the PostgreSQL-only tests are skipped.
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
USING_POSTGRES = bool(TEST_DATABASE_URL)

requires_postgres = pytest.mark.skipif(
    not USING_POSTGRES, reason="TEST_DATABASE_URL is not set"
)

if USING_POSTGRES:
    test_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
else:
    test_engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
TestSessionLocal = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


async def _fresh_postgres_schema() -> None:
    async with test_engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await init_db(test_engine)


@pytest.fixture(scope="function")
async def db_session(monkeypatch) -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for testing."""
    if USING_POSTGRES:
        await _fresh_postgres_schema()
        monkeypatch.setattr(limiter, "_session_factory", TestSessionLocal)
    else:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        async def _unlimited(*_args, **_kwargs) -> None:
            return None

        # rate_limit_buckets is PostgreSQL-only SQL; tests/test_rate_limit.py
        # covers the limiter itself when TEST_DATABASE_URL is set.
        monkeypatch.setattr(limiter, "hit", _unlimited)
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()
    if not USING_POSTGRES:
        async with test_engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create a test client."""

    async def override_get_db():
        # Same contract as the real get_db: commit on success, roll back on error.
        try:
            yield db_session
            await db_session.commit()
        except Exception:
            await db_session.rollback()
            raise

    test_app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=test_app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    test_app.dependency_overrides.clear()


@pytest.fixture
def test_user_data():
    """Fixture for test user data."""
    return {
        "name": "Test User",
        "email": "test@example.com",
        "password": "testpassword123",
    }


@pytest.fixture
async def registered_user(db_session: AsyncSession, test_user_data: dict):
    """Fixture for a registered user."""
    user = User(
        name=test_user_data["name"],
        email=test_user_data["email"],
        hashed_password=get_password_hash(test_user_data["password"]),
        is_verified=True,
        verification_token=None,
    )
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)
    return user


@pytest.fixture
async def authenticated_user(client: AsyncClient, registered_user):
    """Fixture for an authenticated user."""
    response = await client.post(
        "/v1/auth/login",
        json={"email": registered_user.email, "password": "testpassword123"},
    )
    assert response.status_code == 200
    data = response.json()
    return {
        "user": registered_user,
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
    }


@pytest.fixture
def mock_image_file():
    """Fixture for a mock image file."""
    img = Image.new("RGB", (100, 100), color="red")
    img_bytes = BytesIO()
    img.save(img_bytes, format="PNG")
    img_bytes.seek(0)
    return ("test_image.png", img_bytes, "image/png")
