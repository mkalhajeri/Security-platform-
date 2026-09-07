import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from app.auth import ensure_bootstrap_admin
from app.config import get_settings
from app.database import get_database
from app.main import app


@pytest_asyncio.fixture
async def test_db():
    mock_client = AsyncMongoMockClient()
    db = mock_client["security_platform_test"]

    app.dependency_overrides[get_database] = lambda: db
    await ensure_bootstrap_admin(db)
    yield db
    app.dependency_overrides.pop(get_database, None)


@pytest_asyncio.fixture
async def client(test_db):
    # ASGITransport never sends lifespan events, so app startup/shutdown
    # (real MongoDB connection) is never triggered — tests run entirely
    # against the mocked db injected via the dependency override above.
    #
    # Authenticated as the bootstrap admin by default: admin satisfies
    # every role check, so most tests don't need to think about auth at
    # all. Role-gating itself is exercised separately in test_auth.py via
    # as_role(), which returns a client logged in as a specific role.
    transport = ASGITransport(app=app)
    settings = get_settings()
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login = await ac.post(
            "/auth/login",
            json={"email": settings.admin_email, "password": settings.admin_password},
        )
        assert login.status_code == 200, login.text
        ac.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
        yield ac


async def create_user(admin_client, *, email, full_name, role, password="Password123!"):
    """Provision an account via the admin-only endpoint. `admin_client` must
    already be authenticated as an admin (the default `client` fixture is)."""
    resp = await admin_client.post(
        "/users",
        json={"email": email, "full_name": full_name, "role": role, "password": password},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def as_role(admin_client, role, *, email=None, full_name=None, password="Password123!"):
    """Create a fresh account with the given role and return a new
    AsyncClient authenticated as that user (independent of `admin_client`,
    which stays logged in as admin)."""
    email = email or f"{role}@example.com"
    full_name = full_name or role.replace("_", " ").title()
    await create_user(admin_client, email=email, full_name=full_name, role=role, password=password)

    ac = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    login = await ac.post("/auth/login", json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    ac.headers["Authorization"] = f"Bearer {login.json()['access_token']}"
    return ac


def incident_payload(**overrides):
    """A minimal-but-valid IncidentCreate payload, shared across test modules."""
    payload = {
        "site_location": "Warehouse 3 — Jebel Ali",
        "department_area": "Loading Bay",
        "report_date": "2026-09-07",
        "report_time": "14:30",
        "reported_by": "Ahmed Al-Farsi",
        "reported_by_job_title": "Security Officer",
        "reported_by_id_no": "SEC-0042",
        "reported_via": "phone_call",
        "nature_of_report": "security",
        "involved_persons": [
            {
                "name": "John Mensah",
                "designation": "Forklift Operator",
                "company": "ACME Logistics",
                "id_number": "784-1990-1234567-1",
                "nationality": "Ghanaian",
                "contact_no": "+971-50-1234567",
                "gender": "M",
            }
        ],
        "witnesses": "Fatima Noor (Dock Supervisor)",
        "exact_location": "Loading Bay 3, near dock door 7",
        "incident_date": "2026-09-07",
        "incident_time": "14:10",
        "incident_categories": ["theft", "security_breach"],
        "incident_background": "A pallet of electronics went missing between the 13:00 and 14:00 stock checks.",
        "immediate_action_taken": "CCTV footage pulled; access logs reviewed; site supervisor notified.",
        "supporting_documents": ["cctv_footage"],
        "prepared_by": {
            "name": "Ahmed Al-Farsi",
            "position": "Security Officer",
            "signed_date": "2026-09-07",
            "signature": "A. Al-Farsi",
        },
    }
    payload.update(overrides)
    return payload


async def create_incident(client, **overrides):
    return await client.post("/incidents", json=incident_payload(**overrides))
