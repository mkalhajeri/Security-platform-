from tests.conftest import as_role, create_incident


async def test_analytics_empty(client):
    resp = await client.get("/analytics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_incidents"] == 0
    assert body["trend"] == []
    assert body["repeat_involved_persons"] == []


async def test_analytics_trend_and_breakdowns(client):
    await create_incident(
        client,
        site_other="Gate A",
        reported_by="Omar",
        incident_date="2026-01-05",
        incident_categories=["theft"],
        involved_persons=[],
    )
    await create_incident(
        client,
        site_other="Gate A",
        reported_by="Omar",
        incident_date="2026-01-20",
        incident_categories=["theft"],
        involved_persons=[{"name": "John Mensah", "id_number": "784-1"}],
    )
    await create_incident(
        client,
        site_other="Gate A",
        reported_by="Lina",
        incident_date="2026-02-10",
        incident_categories=["theft"],
        involved_persons=[{"name": "John Mensah", "id_number": "784-1"}],
    )
    await create_incident(
        client,
        site_other="Warehouse",
        reported_by="Marcus",
        incident_date="2026-04-01",
        incident_categories=["fire_explosion"],
        involved_persons=[],
    )

    resp = await client.get("/analytics", params={"period": "month"})
    assert resp.status_code == 200
    body = resp.json()

    assert body["total_incidents"] == 4
    assert {p["period"]: p["count"] for p in body["trend"]} == {
        "2026-01": 2,
        "2026-02": 1,
        "2026-04": 1,
    }
    assert {c["key"]: c["count"] for c in body["category_breakdown"]} == {"theft": 3, "fire_explosion": 1}
    assert {s["key"]: s["count"] for s in body["top_sites"]} == {"Gate A": 3, "Warehouse": 1}
    assert {r["key"]: r["count"] for r in body["top_reporters"]} == {"Omar": 2, "Lina": 1, "Marcus": 1}

    assert body["repeat_involved_persons"] == [
        {"name": "John Mensah", "id_number": "784-1", "count": 2, "last_seen": "2026-02-10"}
    ]
    assert body["repeat_site_category_patterns"] == [
        {"site_location": "Gate A", "category": "theft", "count": 3}
    ]


async def test_analytics_quarter_and_year_bucketing(client):
    await create_incident(client, incident_date="2026-01-15")
    await create_incident(client, incident_date="2026-02-15")
    await create_incident(client, incident_date="2026-07-15")

    resp = await client.get("/analytics", params={"period": "quarter"})
    # January and February both roll into 2026-Q1, alongside the July report in Q3.
    by_period = {p["period"]: p["count"] for p in resp.json()["trend"]}
    assert by_period == {"2026-Q1": 2, "2026-Q3": 1}

    resp = await client.get("/analytics", params={"period": "year"})
    by_period = {p["period"]: p["count"] for p in resp.json()["trend"]}
    assert by_period == {"2026": 3}


async def test_analytics_date_range_filter(client):
    await create_incident(client, incident_date="2026-01-05")
    await create_incident(client, incident_date="2026-06-05")

    resp = await client.get("/analytics", params={"from_date": "2026-05-01", "to_date": "2026-12-31"})
    assert resp.json()["total_incidents"] == 1


async def test_analytics_reviewers_and_approvers(client):
    created = (await create_incident(client)).json()

    supervisor = await as_role(client, "security_supervisor", full_name="Lina Haddad")
    await supervisor.patch(
        f"/incidents/{created['id']}",
        json={"reviewed_by": {"signed_date": "2026-09-08"}},
    )
    manager = await as_role(client, "management", full_name="Youssef Kanaan")
    await manager.patch(
        f"/incidents/{created['id']}",
        json={"approved_by": {"signed_date": "2026-09-09"}},
    )

    resp = await client.get("/analytics")
    body = resp.json()
    assert body["top_reviewers"] == [{"key": "Lina Haddad", "count": 1}]
    assert body["top_approvers"] == [{"key": "Youssef Kanaan", "count": 1}]
