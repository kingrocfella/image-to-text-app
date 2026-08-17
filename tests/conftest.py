"""Pytest configuration and fixtures."""
# pylint: disable=import-error,redefined-outer-name,unused-argument,unexpected-keyword-arg,no-member

import os
from contextlib import asynccontextmanager
from io import BytesIO
from typing import AsyncGenerator

os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32-bytes")
os.environ.setdefault("OPENAI_PASS", "test-openai-password")
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("POSTGRES_USER", "test")
os.environ.setdefault("POSTGRES_PASSWORD", "test")
os.environ.setdefault("POSTGRES_DB", "test")
os.environ.setdefault("POSTGRES_HOST", "127.0.0.1")
os.environ.setdefault("POSTGRES_PORT", "5432")

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from PIL import Image
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, User, get_db
from app.routes import router as api_router
from app.utils import get_password_hash

try:
    from httpx import ASGITransport
except ImportError:
    ASGITransport = None

# Configure pytest-asyncio
pytest_plugins = ("pytest_asyncio",)

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"
test_engine = create_async_engine(TEST_DATABASE_URL, echo=False)
TestSessionLocal = async_sessionmaker(
    test_engine, class_=AsyncSession, expire_on_commit=False
)


@asynccontextmanager
async def test_lifespan(_app: FastAPI):
    """Mock lifespan for testing."""
    yield


test_app = FastAPI(title="Test ScanGenAI API", lifespan=test_lifespan)
test_app.include_router(api_router)


@pytest.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a database session for testing."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with TestSessionLocal() as session:
        yield session
        await session.rollback()
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest.fixture(scope="function")
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Create a test client."""

    async def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db
    try:
        if ASGITransport is not None:
            transport = ASGITransport(app=test_app)  # type: ignore[call-arg]
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac
        else:
            raise AttributeError("ASGITransport not available")
    except (TypeError, AttributeError):
        async with AsyncClient(app=test_app, base_url="http://test") as ac:  # type: ignore[call-arg]
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
        "/auth/login",
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
