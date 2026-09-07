import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.database import get_database
from app.main import app


@pytest_asyncio.fixture
async def test_db():
    client = AsyncMongoMockClient()
    db = client["security_platform_test"]

    async def _override_get_database():
        return db

    app.dependency_overrides[get_database] = lambda: db
    yield db
    app.dependency_overrides.pop(get_database, None)


@pytest_asyncio.fixture
async def client(test_db):
    # ASGITransport never sends lifespan events, so app startup/shutdown
    # (real MongoDB connection) is never triggered — tests run entirely
    # against the mocked db injected via the dependency override above.
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
