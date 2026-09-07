import pytest


async def test_root_serves_ui(client):
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "Security Platform" in resp.text


async def test_api_info(client):
    resp = await client.get("/api/info")
    assert resp.status_code == 200
    assert "name" in resp.json()


def _incident_payload(**overrides):
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


async def _create_incident(client, **overrides):
    return await client.post("/incidents", json=_incident_payload(**overrides))


async def test_create_incident(client):
    resp = await _create_incident(client)
    assert resp.status_code == 201
    body = resp.json()
    assert body["site_location"] == "Warehouse 3 — Jebel Ali"
    assert body["status"] == "reported"
    assert body["involved_persons"][0]["name"] == "John Mensah"
    assert body["incident_categories"] == ["theft", "security_breach"]
    assert len(body["timeline"]) == 1
    assert body["timeline"][0]["action"] == "created"
    assert body["id"]


async def test_create_incident_requires_core_fields(client):
    resp = await client.post("/incidents", json={"site_location": "Only this"})
    assert resp.status_code == 422


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
    await _create_incident(client, site_location="Site A", incident_categories=["theft"])
    await _create_incident(client, site_location="Site B", incident_categories=["fire_explosion"])

    resp = await client.get("/incidents", params={"category": "fire_explosion"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["site_location"] == "Site B"

    resp = await client.get("/incidents", params={"site_location": "Site A"})
    assert resp.json()["total"] == 1


async def test_update_status_and_approvals_append_timeline(client):
    created = (await _create_incident(client)).json()

    resp = await client.patch(f"/incidents/{created['id']}", json={"status": "under_review"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "under_review"
    assert len(body["timeline"]) == 2
    assert body["timeline"][-1]["action"] == "status_change"

    resp = await client.patch(
        f"/incidents/{created['id']}",
        json={"reviewed_by": {"name": "Lina Haddad", "position": "Security Supervisor", "signed_date": "2026-09-08"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["reviewed_by"]["name"] == "Lina Haddad"
    assert body["timeline"][-1]["action"] == "reviewed_by"


async def test_add_timeline_entry(client):
    created = (await _create_incident(client)).json()

    resp = await client.post(
        f"/incidents/{created['id']}/timeline",
        json={"actor": "Security Supervisor", "action": "comment", "note": "Escalated to management."},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["timeline"]) == 2
    assert body["timeline"][-1]["note"] == "Escalated to management."


async def test_delete_incident(client):
    created = (await _create_incident(client)).json()

    resp = await client.delete(f"/incidents/{created['id']}")
    assert resp.status_code == 204

    resp = await client.get(f"/incidents/{created['id']}")
    assert resp.status_code == 404


async def test_upload_get_and_delete_attachment(client):
    created = (await _create_incident(client)).json()
    incident_id = created["id"]

    files = {"file": ("dock7.jpg", b"\xff\xd8\xff\xfake-jpeg-bytes", "image/jpeg")}
    resp = await client.post(
        f"/incidents/{incident_id}/attachments",
        params={"kind": "picture"},
        files=files,
        data={"description": "Dock door 7, wide angle"},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert len(body["incident_pictures"]) == 1
    attachment = body["incident_pictures"][0]
    assert attachment["filename"] == "dock7.jpg"
    assert attachment["description"] == "Dock door 7, wide angle"
    assert body["timeline"][-1]["action"] == "attachment_added"

    resp = await client.get(f"/incidents/{incident_id}/attachments/{attachment['id']}")
    assert resp.status_code == 200
    assert resp.content == b"\xff\xd8\xff\xfake-jpeg-bytes"
    assert resp.headers["content-type"] == "image/jpeg"

    resp = await client.delete(f"/incidents/{incident_id}/attachments/{attachment['id']}")
    assert resp.status_code == 200
    assert resp.json()["incident_pictures"] == []

    resp = await client.get(f"/incidents/{incident_id}/attachments/{attachment['id']}")
    assert resp.status_code == 404


async def test_upload_attachment_rejects_empty_file(client):
    created = (await _create_incident(client)).json()
    files = {"file": ("empty.pdf", b"", "application/pdf")}
    resp = await client.post(
        f"/incidents/{created['id']}/attachments",
        params={"kind": "document"},
        files=files,
    )
    assert resp.status_code == 400


async def test_upload_attachment_rejects_oversize_file(client):
    created = (await _create_incident(client)).json()
    huge = b"0" * (8 * 1024 * 1024 + 1)
    files = {"file": ("big.pdf", huge, "application/pdf")}
    resp = await client.post(
        f"/incidents/{created['id']}/attachments",
        params={"kind": "document"},
        files=files,
    )
    assert resp.status_code == 400
