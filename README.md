# Security Platform

A security platform, starting with a **physical security incident report
system**, a **web UI** for it, and the shared data store behind both. The
data model mirrors the organization's existing paper "Security Incident
Report" form section-for-section, so anyone familiar with the paper
process can use the digital one immediately. More modules (patrols, access
control, asset inventory) are expected to plug into the same FastAPI app
and MongoDB database over time.

## Stack

- **API:** Python 3.11+, [FastAPI](https://fastapi.tiangolo.com/)
- **UI:** Static HTML/CSS/vanilla JS single-page app, served directly by the API — no build step, no external dependencies
- **Database:** MongoDB, accessed asynchronously via [Motor](https://motor.readthedocs.io/)
- **File storage:** Incident photos and supporting documents are stored as binary data in MongoDB (an `attachments` collection), capped at 8 MB per file — no separate object storage needed
- **Tests:** pytest + httpx, against an in-memory MongoDB mock (no real database needed to run the test suite)

## Project layout

```
app/
  main.py             FastAPI app, startup/shutdown, router registration
  config.py           Settings loaded from environment variables / .env
  database.py         MongoDB connection lifecycle + index creation
  models.py           Pydantic models/schemas for incidents (mirrors the paper form's 12 sections)
  routers/
    health.py         GET /health
    incidents.py       Incident CRUD, timeline, and attachment endpoints
  static/
    index.html        UI layout (incident queue + the multi-section report form)
    styles.css        UI styling
    app.js            UI logic (fetches the /incidents API)
tests/
  conftest.py         Test fixtures (mocked MongoDB, HTTP test client)
  test_incidents.py   API tests covering incidents and attachments
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

### 4. Run the app

```bash
uvicorn app.main:app --reload
```

- Web UI: http://localhost:8000/
- Interactive API docs: http://localhost:8000/docs
- Health check: http://localhost:8000/health

Or with Docker (bundles the API + MongoDB, no local Python/Mongo install needed):

```bash
docker compose up --build
```

Then open http://localhost:8000/.

### 5. Run the tests

```bash
pytest
```

Tests run against `mongomock-motor`, an in-memory MongoDB mock, so no
running database is required.

## Data model: Incident

Stored in the `incidents` collection. Section numbers refer to the paper
"Security Incident Report" form this mirrors.

| Section | Field(s) | Notes |
|---|---|---|
| System | `id`, `status` | `status` is `reported` \| `under_review` \| `closed` — a digital workflow state not on the paper form |
| 1. Reporting Details | `site_location`, `department_area`, `report_date`, `report_time`, `reported_by`, `reported_by_job_title`, `reported_by_id_no`, `reported_via` (`email`\|`phone_call`\|`sms`\|`whatsapp`\|`other`), `nature_of_report` (`incident`\|`accident`\|`near_miss`\|`security`\|`other`) | |
| 2. Involved Person(s) | `involved_persons`: list of `{name, designation, company, id_number, nationality, contact_no, gender}`; `witnesses` (free text) | Repeatable rows |
| 3. Incident Details | `incident_type_summary`, `exact_location`, `incident_date`, `incident_time` | |
| 4. Type of Incident | `incident_categories`: list of 20 categories (vehicle accident, injury, fire/explosion, theft, security breach, physical assault, …) + `incident_category_other` | Multi-select |
| 5–8, 10. Narrative | `incident_background`, `immediate_action_taken`, `root_cause`, `recommendations`, `local_authorities_involvement` | Free text |
| 9. Incident Pictures | `incident_pictures`: list of attachment metadata `{id, filename, content_type, size, description, uploaded_at}` | Files uploaded separately, see below |
| 11. Supporting Documents | `supporting_documents`: list of 8 document types (photos, CCTV footage, police report, …) + `supporting_documents_other`; `supporting_document_files`: uploaded file metadata | |
| 12. Approvals | `prepared_by`, `reviewed_by`, `approved_by`: each `{name, position, signed_date, signature}` | Typed signature, not e-signature |
| — | `timeline`: list of `{timestamp, actor, action, note}` | Auto-appended on creation, status changes, sign-offs, and attachment changes; can also be appended to manually |
| — | `created_at` / `updated_at` (UTC) | Managed by the API |

## API endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Web UI (static SPA) |
| GET | `/api/info` | API name/version metadata |
| GET | `/health` | Liveness/readiness check (pings MongoDB) |
| POST | `/incidents` | Report a new incident |
| GET | `/incidents` | List incidents (filter by `status`, `nature_of_report`, `category`, `site_location`, `search`; paginate with `limit`/`offset`) |
| GET | `/incidents/{id}` | Get one incident |
| PATCH | `/incidents/{id}` | Update fields (partial); status changes and new sign-offs are recorded in the timeline |
| POST | `/incidents/{id}/timeline` | Append a manual timeline entry/comment |
| DELETE | `/incidents/{id}` | Delete an incident (and its attachments) |
| POST | `/incidents/{id}/attachments?kind=picture\|document` | Upload a file (multipart/form-data, field `file`, optional `description`); max 8 MB |
| GET | `/incidents/{id}/attachments/{attachment_id}` | Download/view a file |
| DELETE | `/incidents/{id}/attachments/{attachment_id}` | Remove a file |

### Example: report an incident

```bash
curl -X POST http://localhost:8000/incidents \
  -H "Content-Type: application/json" \
  -d '{
        "site_location": "Warehouse 3 — Jebel Ali",
        "department_area": "Loading Bay",
        "report_date": "2026-09-07",
        "report_time": "14:30",
        "reported_by": "Ahmed Al-Farsi",
        "reported_by_job_title": "Security Officer",
        "reported_via": "phone_call",
        "nature_of_report": "security",
        "exact_location": "Loading Bay 3, near dock door 7",
        "incident_date": "2026-09-07",
        "incident_time": "14:10",
        "incident_categories": ["theft", "security_breach"],
        "incident_background": "A pallet of electronics went missing between the 13:00 and 14:00 stock checks."
      }'
```

### Example: attach a photo

```bash
curl -X POST "http://localhost:8000/incidents/<id>/attachments?kind=picture" \
  -F "file=@dock7.jpg" \
  -F "description=Dock door 7, wide angle"
```

## Roadmap

Incident reporting + storage (matching the paper form), file attachments,
a Docker-based local setup, and a web UI are done. Natural next steps:

- Authentication & authorization (tie incidents/sign-offs to real accounts, restrict who can update/delete/approve)
- Notifications/webhooks on new reports or status changes
- Reporting/export (e.g. generate a PDF matching the original paper layout from a stored incident)
- Additional platform modules (patrol logs, access control, asset inventory) sharing the same database

## Notes on the database driver

The API uses [Motor](https://motor.readthedocs.io/) for async MongoDB
access. MongoDB has announced Motor's deprecation in favor of PyMongo's
native async driver (`pymongo.AsyncMongoClient`, available since PyMongo
4.9). Motor remains fully functional today and has the most mature
ecosystem for testing (`mongomock-motor`), so it's the pragmatic choice
for this milestone — but migrating `app/database.py` to PyMongo's native
async client is worth revisiting later.
