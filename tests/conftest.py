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
