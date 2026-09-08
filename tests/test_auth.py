from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import app
from tests.conftest import as_role, create_incident, create_user


async def unauthenticated_client():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------


async def test_login_succeeds_with_correct_credentials(test_db):
    settings = get_settings()
    anon = await unauthenticated_client()
    resp = await anon.post("/auth/login", json={"email": settings.admin_email, "password": settings.admin_password})
    assert resp.status_code == 200
    body = resp.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["user"]["role"] == "admin"
    assert "hashed_password" not in body["user"]


async def test_login_fails_with_wrong_password(test_db):
    settings = get_settings()
    anon = await unauthenticated_client()
    resp = await anon.post("/auth/login", json={"email": settings.admin_email, "password": "wrong-password"})
    assert resp.status_code == 401


async def test_login_fails_for_unknown_email(test_db):
    anon = await unauthenticated_client()
    resp = await anon.post("/auth/login", json={"email": "nobody@example.com", "password": "whatever123"})
    assert resp.status_code == 401


async def test_disabled_account_cannot_log_in(client):
    user = await as_role(client, "security_officer", email="officer@example.com", password="Password123!")
    me = await user.get("/auth/me")
    user_id = me.json()["id"]

    resp = await client.patch(f"/users/{user_id}", json={"is_active": False})
    assert resp.status_code == 200

    anon = await unauthenticated_client()
    resp = await anon.post("/auth/login", json={"email": "officer@example.com", "password": "Password123!"})
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Protected endpoints require a token
# ---------------------------------------------------------------------------


async def test_incidents_require_authentication(test_db):
    # test_db (unused directly) puts a mock database behind get_database —
    # in a real deployment the DB is always connected before any request
    # is served; only the test harness needs this made explicit.
    anon = await unauthenticated_client()
    resp = await anon.get("/incidents")
    assert resp.status_code == 401


async def test_analytics_require_authentication(test_db):
    anon = await unauthenticated_client()
    resp = await anon.get("/analytics")
    assert resp.status_code == 401


async def test_invalid_token_is_rejected(test_db):
    anon = await unauthenticated_client()
    anon.headers["Authorization"] = "Bearer not-a-real-token"
    resp = await anon.get("/incidents")
    assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Role hierarchy on sign-offs
# ---------------------------------------------------------------------------


async def test_officer_cannot_sign_reviewed_by(client):
    created = (await create_incident(client)).json()
    officer = await as_role(client, "security_officer", full_name="Omar Al-Rashidi")

    resp = await officer.patch(f"/incidents/{created['id']}", json={"reviewed_by": {"signed_date": "2026-09-08"}})
    assert resp.status_code == 403


async def test_supervisor_can_sign_reviewed_by_but_not_approved_by(client):
    created = (await create_incident(client)).json()
    supervisor = await as_role(client, "security_supervisor", full_name="Lina Haddad")

    resp = await supervisor.patch(f"/incidents/{created['id']}", json={"reviewed_by": {"signed_date": "2026-09-08"}})
    assert resp.status_code == 200
    assert resp.json()["reviewed_by"]["name"] == "Lina Haddad"

    resp = await supervisor.patch(f"/incidents/{created['id']}", json={"approved_by": {"signed_date": "2026-09-08"}})
    assert resp.status_code == 403


async def test_management_can_sign_approved_by(client):
    created = (await create_incident(client)).json()
    manager = await as_role(client, "management", full_name="Youssef Kanaan")

    resp = await manager.patch(f"/incidents/{created['id']}", json={"approved_by": {"signed_date": "2026-09-08"}})
    assert resp.status_code == 200
    assert resp.json()["approved_by"]["name"] == "Youssef Kanaan"


async def test_signoff_name_cannot_be_spoofed(client):
    """The client-supplied name/position in a sign-off payload is ignored —
    the signer's identity always comes from their login."""
    created = (await create_incident(client)).json()
    supervisor = await as_role(client, "security_supervisor", full_name="Real Signer")

    resp = await supervisor.patch(
        f"/incidents/{created['id']}",
        json={"reviewed_by": {"name": "Someone Else Entirely", "signed_date": "2026-09-08"}},
    )
    assert resp.status_code == 200
    assert resp.json()["reviewed_by"]["name"] == "Real Signer"


async def test_timeline_actor_comes_from_login_not_request_body(client):
    created = (await create_incident(client)).json()
    officer = await as_role(client, "security_officer", full_name="Real Commenter")

    resp = await officer.post(
        f"/incidents/{created['id']}/timeline",
        json={"action": "comment", "note": "Checked the area."},
    )
    assert resp.status_code == 201
    assert resp.json()["timeline"][-1]["actor"] == "Real Commenter"


# ---------------------------------------------------------------------------
# Delete is admin-only
# ---------------------------------------------------------------------------


async def test_non_admin_cannot_delete_incident(client):
    created = (await create_incident(client)).json()
    manager = await as_role(client, "management")

    resp = await manager.delete(f"/incidents/{created['id']}")
    assert resp.status_code == 403


async def test_admin_can_delete_incident(client):
    created = (await create_incident(client)).json()
    resp = await client.delete(f"/incidents/{created['id']}")
    assert resp.status_code == 204


# ---------------------------------------------------------------------------
# User management: Admin and Management can provision accounts; Management
# is restricted to Security Officer / Security Supervisor accounts only —
# it can't touch a peer Management account or an Admin account, and can't
# assign either of those roles. Below Management, no one can manage users.
# ---------------------------------------------------------------------------


async def test_officer_and_supervisor_cannot_manage_users(client):
    for role in ("security_officer", "security_supervisor"):
        actor = await as_role(client, role)
        resp = await actor.get("/users")
        assert resp.status_code == 403

        resp = await actor.post(
            "/users",
            json={"email": f"new-{role}@example.com", "full_name": "New Person", "role": "security_officer", "password": "Password123!"},
        )
        assert resp.status_code == 403


async def test_management_can_create_and_manage_junior_accounts(client):
    manager = await as_role(client, "management", full_name="Nadia Suleiman")

    resp = await manager.post(
        "/users",
        json={"email": "junior@example.com", "full_name": "Junior Officer", "role": "security_officer", "password": "Password123!"},
    )
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]

    resp = await manager.get("/users")
    assert resp.status_code == 200
    assert any(u["email"] == "junior@example.com" for u in resp.json())

    resp = await manager.patch(f"/users/{user_id}", json={"role": "security_supervisor"})
    assert resp.status_code == 200
    assert resp.json()["role"] == "security_supervisor"

    resp = await manager.delete(f"/users/{user_id}")
    assert resp.status_code == 204


async def test_management_cannot_manage_peer_or_admin_accounts(client):
    manager = await as_role(client, "management", full_name="Nadia Suleiman", email="nadia@example.com")
    other_manager = (await client.get("/auth/me")).json()  # bootstrap admin, for a same-request baseline
    peer = await create_user(client, email="peer-mgmt@example.com", full_name="Peer Manager", role="management")

    # Can't touch a peer Management account...
    resp = await manager.patch(f"/users/{peer['id']}", json={"is_active": False})
    assert resp.status_code == 403

    # ...or an Admin account.
    resp = await manager.patch(f"/users/{other_manager['id']}", json={"is_active": False})
    assert resp.status_code == 403

    resp = await manager.delete(f"/users/{peer['id']}")
    assert resp.status_code == 403


async def test_management_cannot_assign_management_or_admin_role(client):
    manager = await as_role(client, "management")
    officer = await create_user(client, email="officer2@example.com", full_name="Some Officer", role="security_officer")

    resp = await manager.post(
        "/users",
        json={"email": "wannabe-admin@example.com", "full_name": "Wannabe", "role": "admin", "password": "Password123!"},
    )
    assert resp.status_code == 403

    resp = await manager.patch(f"/users/{officer['id']}", json={"role": "management"})
    assert resp.status_code == 403


async def test_admin_can_create_and_list_users(client):
    resp = await client.post(
        "/users",
        json={"email": "priya@example.com", "full_name": "Priya Nandakumar", "role": "security_supervisor", "password": "Password123!"},
    )
    assert resp.status_code == 201
    assert "hashed_password" not in resp.json()

    resp = await client.get("/users")
    emails = [u["email"] for u in resp.json()]
    assert "priya@example.com" in emails


async def test_cannot_create_duplicate_email(client):
    await client.post(
        "/users",
        json={"email": "dup@example.com", "full_name": "First", "role": "security_officer", "password": "Password123!"},
    )
    resp = await client.post(
        "/users",
        json={"email": "dup@example.com", "full_name": "Second", "role": "security_officer", "password": "Password123!"},
    )
    assert resp.status_code == 409


async def test_admin_cannot_remove_own_admin_role(client):
    me = (await client.get("/auth/me")).json()
    resp = await client.patch(f"/users/{me['id']}", json={"role": "security_officer"})
    assert resp.status_code == 400


async def test_admin_cannot_deactivate_self(client):
    me = (await client.get("/auth/me")).json()
    resp = await client.patch(f"/users/{me['id']}", json={"is_active": False})
    assert resp.status_code == 400


async def test_admin_cannot_delete_self(client):
    me = (await client.get("/auth/me")).json()
    resp = await client.delete(f"/users/{me['id']}")
    assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Password change
# ---------------------------------------------------------------------------


async def test_change_own_password(client):
    officer = await as_role(client, "security_officer", email="pw-test@example.com", password="OldPassword123!")

    resp = await officer.post(
        "/auth/change-password",
        json={"current_password": "OldPassword123!", "new_password": "NewPassword456!"},
    )
    assert resp.status_code == 204

    anon = await unauthenticated_client()
    resp = await anon.post("/auth/login", json={"email": "pw-test@example.com", "password": "NewPassword456!"})
    assert resp.status_code == 200


async def test_change_password_rejects_wrong_current_password(client):
    officer = await as_role(client, "security_officer", email="pw-test2@example.com", password="OldPassword123!")
    resp = await officer.post(
        "/auth/change-password",
        json={"current_password": "totally-wrong", "new_password": "NewPassword456!"},
    )
    assert resp.status_code == 401
