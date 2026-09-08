"""Sites registry (admin-managed) and the incident numbering it seeds."""

from tests.conftest import as_role, create_incident


async def _create_site(client, code="JAW", name="Jebel Ali Warehouse"):
    resp = await client.post("/sites", json={"code": code, "name": name})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_admin_can_create_and_list_sites(client):
    site = await _create_site(client)
    assert site["code"] == "JAW"
    assert site["is_active"] is True

    resp = await client.get("/sites")
    assert resp.status_code == 200
    assert [s["code"] for s in resp.json()] == ["JAW"]


async def test_site_code_is_normalized_and_must_be_unique(client):
    resp = await client.post("/sites", json={"code": " jaw ", "name": "Jebel Ali Warehouse"})
    assert resp.status_code == 201
    assert resp.json()["code"] == "JAW"

    dup = await client.post("/sites", json={"code": "JAW", "name": "Duplicate"})
    assert dup.status_code == 409


async def test_officer_and_supervisor_can_list_but_not_create_sites(client):
    for role in ("security_officer", "security_supervisor"):
        actor = await as_role(client, role)
        resp = await actor.get("/sites")
        assert resp.status_code == 200

        resp = await actor.post("/sites", json={"code": "ABC", "name": "Some Site"})
        assert resp.status_code == 403


async def test_management_can_create_update_and_delete_sites(client):
    manager = await as_role(client, "management")

    resp = await manager.post("/sites", json={"code": "MGT", "name": "Management-added Site"})
    assert resp.status_code == 201, resp.text
    site = resp.json()

    resp = await manager.patch(f"/sites/{site['id']}", json={"name": "Renamed Site"})
    assert resp.status_code == 200
    assert resp.json()["name"] == "Renamed Site"

    resp = await manager.delete(f"/sites/{site['id']}")
    assert resp.status_code == 204


async def test_deactivated_site_is_hidden_by_default_but_visible_with_flag(client):
    site = await _create_site(client)
    await client.patch(f"/sites/{site['id']}", json={"is_active": False})

    resp = await client.get("/sites")
    assert resp.json() == []

    resp = await client.get("/sites", params={"include_inactive": True})
    assert len(resp.json()) == 1
    assert resp.json()[0]["is_active"] is False


async def test_site_with_incidents_cannot_be_deleted(client):
    site = await _create_site(client)
    await create_incident(client, site_id=site["id"], site_other=None)

    resp = await client.delete(f"/sites/{site['id']}")
    assert resp.status_code == 409


async def test_deleting_unused_site_succeeds(client):
    site = await _create_site(client)
    resp = await client.delete(f"/sites/{site['id']}")
    assert resp.status_code == 204


async def test_incident_number_uses_registered_site_code_and_year(client):
    site = await _create_site(client)
    created = (await create_incident(client, site_id=site["id"], site_other=None, incident_date="2026-03-01")).json()

    assert created["incident_number"] == "JAW-2026-0001"
    assert created["site_id"] == site["id"]
    assert created["site_code"] == "JAW"
    assert created["site_location"] == "Jebel Ali Warehouse"


async def test_incident_number_sequence_is_per_site_per_year(client):
    site_a = await _create_site(client, code="AAA", name="Site A")
    site_b = await _create_site(client, code="BBB", name="Site B")

    first = (await create_incident(client, site_id=site_a["id"], site_other=None, incident_date="2026-01-01")).json()
    second = (await create_incident(client, site_id=site_a["id"], site_other=None, incident_date="2026-06-01")).json()
    other_site = (await create_incident(client, site_id=site_b["id"], site_other=None, incident_date="2026-06-01")).json()
    next_year = (await create_incident(client, site_id=site_a["id"], site_other=None, incident_date="2027-01-01")).json()

    assert first["incident_number"] == "AAA-2026-0001"
    assert second["incident_number"] == "AAA-2026-0002"
    assert other_site["incident_number"] == "BBB-2026-0001"
    assert next_year["incident_number"] == "AAA-2027-0001"


async def test_incident_filed_as_other_uses_oth_code(client):
    created = (await create_incident(client, site_other="A one-off location")).json()

    assert created["site_id"] is None
    assert created["site_code"] == "OTH"
    assert created["site_location"] == "A one-off location"
    assert created["incident_number"].startswith("OTH-")


async def test_create_incident_rejects_unknown_site_id(client):
    resp = await client.post(
        "/incidents",
        json={**_minimal_incident_fields(), "site_id": "000000000000000000000000"},
    )
    assert resp.status_code == 400


async def test_create_incident_rejects_inactive_site(client):
    site = await _create_site(client)
    await client.patch(f"/sites/{site['id']}", json={"is_active": False})

    resp = await client.post(
        "/incidents",
        json={**_minimal_incident_fields(), "site_id": site["id"]},
    )
    assert resp.status_code == 400


async def test_list_incidents_filters_by_site_id_and_incident_number(client):
    site = await _create_site(client)
    matching = (await create_incident(client, site_id=site["id"], site_other=None)).json()
    await create_incident(client)  # a different, "Other" site

    resp = await client.get("/incidents", params={"site_id": site["id"]})
    assert resp.json()["total"] == 1
    assert resp.json()["items"][0]["id"] == matching["id"]

    resp = await client.get("/incidents", params={"incident_number": matching["incident_number"]})
    assert resp.json()["total"] == 1

    prefix = matching["incident_number"].rsplit("-", 1)[0]  # e.g. "JAW-2026"
    resp = await client.get("/incidents", params={"incident_number": prefix})
    assert resp.json()["total"] == 1


def _minimal_incident_fields():
    from tests.conftest import incident_payload

    payload = incident_payload()
    payload.pop("site_other", None)
    return payload
