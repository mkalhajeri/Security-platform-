# Security Platform

A security platform, starting with an **incident report system** and the
shared data store behind it. Built as a REST API today; more modules
(assets, users, alerts, integrations) are expected to plug into the same
FastAPI app and MongoDB database over time.

## Stack

- **API:** Python 3.11+, [FastAPI](https://fastapi.tiangolo.com/)
- **Database:** MongoDB, accessed asynchronously via [Motor](https://motor.readthedocs.io/)
- **Tests:** pytest + httpx, against an in-memory MongoDB mock (no real database needed to run the test suite)

## Project layout

```
app/
  main.py             FastAPI app, startup/shutdown, router registration
  config.py           Settings loaded from environment variables / .env
  database.py         MongoDB connection lifecycle + index creation
  models.py           Pydantic models/schemas for incidents
  routers/
    health.py         GET /health
    incidents.py       Incident CRUD + timeline endpoints
tests/
  conftest.py         Test fixtures (mocked MongoDB, HTTP test client)
  test_incidents.py   API tests covering the incident endpoints
```

## Getting started

### 1. Prerequisites

- Python 3.11+
- A MongoDB instance reachable from the app (local install, Docker, or Atlas)

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# for running tests too:
pip install -r requirements-dev.txt
```

### 3. Configure environment

```bash
cp .env.example .env
# edit .env if your MongoDB isn't at the default local address
```

### 4. Run the API

```bash
uvicorn app.main:app --reload
```

- Interactive API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

### 5. Run the tests

```bash
pytest
```

Tests run against `mongomock-motor`, an in-memory MongoDB mock, so no
running database is required.

## Data model: Incident

Stored in the `incidents` collection.

| Field              | Type                                            | Notes                                    |
|--------------------|--------------------------------------------------|-------------------------------------------|
| `id`               | string                                          | Mongo `_id`, returned as a string          |
| `title`            | string                                          | Required                                   |
| `description`      | string                                          | Required                                   |
| `severity`         | `low` \| `medium` \| `high` \| `critical`       | Default `medium`                           |
| `status`           | `open` \| `investigating` \| `contained` \| `resolved` \| `closed` | Defaults to `open` on creation |
| `category`         | `malware` \| `phishing` \| `data_breach` \| `unauthorized_access` \| `denial_of_service` \| `insider_threat` \| `vulnerability` \| `policy_violation` \| `other` | |
| `reporter_name`    | string                                          | Required                                   |
| `reporter_email`   | string (email)                                  | Optional                                   |
| `affected_systems` | list of strings                                 | Free-form asset/system identifiers         |
| `tags`             | list of strings                                 | Free-form labels                           |
| `timeline`         | list of `{timestamp, actor, action, note}`      | Auto-appended on creation and status/severity changes; can also be appended to manually |
| `created_at` / `updated_at` / `resolved_at` | datetime (UTC)         | Managed by the API                         |

## API endpoints

| Method | Path                              | Description                                  |
|--------|------------------------------------|-----------------------------------------------|
| GET    | `/health`                          | Liveness/readiness check (pings MongoDB)      |
| POST   | `/incidents`                       | Report a new incident                         |
| GET    | `/incidents`                       | List incidents (filter by `status`, `severity`, `category`, `search`; paginate with `limit`/`offset`) |
| GET    | `/incidents/{id}`                  | Get one incident                              |
| PATCH  | `/incidents/{id}`                  | Update fields (partial); status/severity changes are recorded in the timeline |
| POST   | `/incidents/{id}/timeline`         | Append a manual timeline entry/comment        |
| DELETE | `/incidents/{id}`                  | Delete an incident                            |

### Example: report an incident

```bash
curl -X POST http://localhost:8000/incidents \
  -H "Content-Type: application/json" \
  -d '{
        "title": "Suspicious login from unknown IP",
        "description": "Multiple failed logins followed by a success from a new location.",
        "severity": "high",
        "category": "unauthorized_access",
        "reporter_name": "Alex Amod",
        "reporter_email": "alex7amod@gmail.com",
        "affected_systems": ["auth-service"],
        "tags": ["login", "anomaly"]
      }'
```

## Roadmap

This is the first milestone (incident reporting + storage). Natural next
steps for the platform:

- Authentication & authorization (tie incidents to accounts, restrict who can update/delete)
- Basic web UI for submitting and triaging incidents
- File/evidence attachment storage (e.g. object storage + metadata in Mongo)
- Notifications/webhooks on new or updated incidents
- Docker Compose setup for one-command local development
- Additional platform modules (asset inventory, vulnerability tracking, alerting) sharing the same database

## Notes on the database driver

The API uses [Motor](https://motor.readthedocs.io/) for async MongoDB
access. MongoDB has announced Motor's deprecation in favor of PyMongo's
native async driver (`pymongo.AsyncMongoClient`, available since PyMongo
4.9). Motor remains fully functional today and has the most mature
ecosystem for testing (`mongomock-motor`), so it's the pragmatic choice
for this milestone — but migrating `app/database.py` to PyMongo's native
async client is worth revisiting later.
