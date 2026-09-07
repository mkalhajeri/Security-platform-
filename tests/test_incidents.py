async def test_root_serves_ui(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Security Platform" in resp.text


async def test_api_info(client):
    resp = await client.get("/api/info")
    assert resp.status_code == 200
    assert "name" in resp.json()


async def _create_incident(client, **overrides):
    payload = {
        "title": "Suspicious login from unknown IP",
        "description": "Multiple failed login attempts followed by a success from a new location.",
        "severity": "high",
        "category": "unauthorized_access",
        "reporter_name": "Alex Amod",
        "reporter_email": "alex7amod@gmail.com",
        "affected_systems": ["auth-service"],
        "tags": ["login", "anomaly"],
    }
    payload.update(overrides)
    return await client.post("/incidents", json=payload)


async def test_create_incident(client):
    resp = await _create_incident(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["title"] == "Suspicious login from unknown IP"
    assert body["status"] == "open"
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["action"] == "created"
    assert body["id"]


async def test_get_incident(client):
    created = (await _create_incident(client)).json()
    resp = await client.get(f"/incidents/{created['id']}")
    assert resp.status_code == 200
    assert resp.json()["id"] == created["id"]


async def test_get_incident_not_found(client):
    resp = await client.get("/incidents/000000000000000000000000")
    assert resp.status_code == 404


async def test_get_incident_invalid_id(client):
    resp = await client.get("/incidents/not-a-valid-id")
    assert resp.status_code == 404


async def test_list_incidents_with_filters(client):
    await _create_incident(client, severity="low", title="Low sev issue")
    await _create_incident(client, severity="critical", title="Critical breach")

    resp = await client.get("/incidents", params={"severity": "critical"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Critical breach"


async def test_update_incident_status_appends_timeline_and_sets_resolved_at(client):
    created = (await _create_incident(client)).json()

    resp = await client.patch(f"/incidents/{created['id']}", json={"status": "resolved"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolved_at"] is not None
    assert len(body["timeline"]) == 2
    assert body["timeline"][-1]["action"] == "status_change"


async def test_add_timeline_entry(client):
    created = (await _create_incident(client)).json()

    resp = await client.post(
        f"/incidents/{created['id']}/timeline",
        json={"actor": "responder", "action": "comment", "note": "Investigating now."},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["timeline"]) == 2
    assert body["timeline"][-1]["note"] == "Investigating now."


async def test_delete_incident(client):
    created = (await _create_incident(client)).json()

    resp = await client.delete(f"/incidents/{created['id']}")
    assert resp.status_code == 204

    resp = await client.get(f"/incidents/{created['id']}")
    assert resp.status_code == 404
