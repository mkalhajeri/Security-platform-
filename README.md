# Security Platform

A security platform, starting with a **physical security incident report
system**, restricted to **logged-in security department staff** with
role-based permissions, plus a **web UI** and the shared data store behind
it all. The data model mirrors the organization's existing paper "Security
Incident Report" form section-for-section, so anyone familiar with the
paper process can use the digital one immediately. More modules (patrols,
access control, asset inventory) are expected to plug into the same
FastAPI app and MongoDB database over time.

## Stack

- **API:** Python 3.11+, [FastAPI](https://fastapi.tiangolo.com/)
- **Auth:** JWT bearer tokens ([PyJWT](https://pyjwt.readthedocs.io/)) + [bcrypt](https://pypi.org/project/bcrypt/) password hashing — no public sign-up, see [Authentication & roles](#authentication--roles)
- **UI:** Static HTML/CSS/vanilla JS single-page app, served directly by the API — no build step, no external dependencies
- **Database:** MongoDB, accessed asynchronously via [Motor](https://motor.readthedocs.io/)
- **File storage:** Incident photos and supporting documents are stored as binary data in MongoDB (an `attachments` collection), capped at 8 MB per file — no separate object storage needed
- **PDF export:** [ReportLab](https://pypi.org/project/reportlab/) + [Pillow](https://pypi.org/project/Pillow/) render a stored incident back into a PDF matching the paper form's own section layout — no system libraries needed (unlike HTML-to-PDF tools), which keeps the Docker image lean
- **Tests:** pytest + httpx, against an in-memory MongoDB mock (no real database needed to run the test suite)

## Project layout

```
app/
  main.py             FastAPI app, startup/shutdown, router registration, bootstrap admin
  config.py           Settings loaded from environment variables / .env
  database.py         MongoDB connection lifecycle + index creation
  auth.py             Password hashing, JWT issuance/verification, auth dependencies
  auth_models.py       Pydantic models for accounts/roles/tokens
  models.py           Pydantic models/schemas for incidents (mirrors the paper form's 12 sections)
  pdf_export.py        Renders a stored incident to a PDF matching the paper form's layout
  routers/
    health.py         GET /health
    auth.py           POST /auth/login, GET /auth/me, POST /auth/change-password
    users.py          Admin-only account management (create/list/update/delete)
    incidents.py       Incident CRUD, timeline, attachment, and PDF-export endpoints — all auth-gated
    analytics.py       Statistics: volume trends, top people, repeat patterns — auth-gated
  static/
    index.html        UI layout (login screen, incident queue, new-report form, analytics, users, signature modal)
    styles.css        UI styling (light theme)
    app.js            UI logic (auth/session handling + fetches the API)
tests/
  conftest.py         Test fixtures (mocked MongoDB, authenticated HTTP client, role helpers)
  test_auth.py        Login, role gating, sign-off identity binding, user management
  test_incidents.py   API tests covering incidents and attachments
  test_analytics.py   API tests covering the statistics endpoint
  test_pdf_export.py  PDF generation, embedded images/signatures, and the enum-stringification regression
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
# edit .env if your MongoDB isn't at the default local address, and set a
# real JWT_SECRET_KEY + ADMIN_PASSWORD before this ever runs anywhere but
# your own machine — see Authentication & roles below
```

### 4. Run the app

```bash
uvicorn app.main:app --reload
```

- Web UI: http://localhost:8000/ — log in with the bootstrap admin credentials from your `.env` (default: `admin@example.com` / `ChangeMe123!`)
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
| 12. Approvals | `prepared_by`, `reviewed_by`, `approved_by`: each `{name, position, signed_date, signature, signature_image}` | `signature` is typed text; `signature_image` is a hand-drawn signature captured on a canvas pad, stored as a base64 PNG data URI (max 300 KB). For `reviewed_by`/`approved_by`, `name`/`position` are always server-set from the signer's account — see [Authentication & roles](#authentication--roles). Not yet a *reusable* per-person signature library (save once, reuse on future documents) — each sign-off is still drawn fresh; see Roadmap. |
| — | `created_by`: `{id, name}` | Who actually filed the record through the system (from their login) — distinct from the free-text `reported_by` paper-form field, which may name someone else (e.g. a supervisor logging what a guard called in) |
| — | `timeline`: list of `{timestamp, actor, action, note}` | `actor` comes from the logged-in user, not client input. Auto-appended on creation, status changes, sign-offs, and attachment changes; can also be appended to manually |
| — | `created_at` / `updated_at` (UTC) | Managed by the API |

## Authentication & roles

There is **no public sign-up**. This is an internal tool for a specific
security department, so accounts are provisioned by an admin for named
staff, and every `/incidents` and `/analytics` endpoint requires a logged-in
account.

**Roles**, matching the paper form's own Section 12 sign-off hierarchy
(each level also has everything the levels below it have):

| Role | Can do |
|---|---|
| `security_officer` | File and view incident reports; prepare (Section 12 "Prepared By") |
| `security_supervisor` | + Sign off as "Reviewed By" |
| `management` | + Sign off as "Approved By" |
| `admin` | + Delete incidents; create/edit/deactivate/delete user accounts |

A sign-off's name and position always come from **whoever is logged in**,
never from the request body — a Supervisor can't sign a review "as" someone
else, and the server rejects the attempt at the role check before it ever
looks at the name field.

**Bootstrap admin:** on first startup, if no admin account exists yet, one
is created automatically from `ADMIN_EMAIL` / `ADMIN_PASSWORD` /
`ADMIN_FULL_NAME` (see `.env.example`). Log in and change that password
immediately in any deployment beyond your own machine — the app logs a
warning on startup if you're still using the default.

**Session tokens:** login returns a JWT bearer token (`Authorization:
Bearer <token>` header), valid for `ACCESS_TOKEN_EXPIRE_MINUTES` (default
12 hours). There's no refresh-token flow yet — a session simply asks you to
log in again once it expires.

## API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | — | Web UI (static SPA) |
| GET | `/api/info` | — | API name/version metadata |
| GET | `/health` | — | Liveness/readiness check (pings MongoDB) |
| POST | `/auth/login` | — | Log in with `{email, password}`, returns a bearer token + user profile |
| GET | `/auth/me` | any | Current user's own profile |
| POST | `/auth/change-password` | any | Change your own password (`{current_password, new_password}`) |
| POST | `/users` | admin | Create an account (`{email, full_name, role, password}`) |
| GET | `/users` | admin | List all accounts |
| PATCH | `/users/{id}` | admin | Update name/role/active-status/password |
| DELETE | `/users/{id}` | admin | Delete an account |
| POST | `/incidents` | any | Report a new incident |
| GET | `/incidents` | any | List incidents (filter by `status`, `nature_of_report`, `category`, `site_location`, `search`; paginate with `limit`/`offset`) |
| GET | `/incidents/{id}` | any | Get one incident |
| PATCH | `/incidents/{id}` | any\* | Update fields (partial); status changes and new sign-offs are recorded in the timeline. \*Setting `reviewed_by` needs `security_supervisor`+, `approved_by` needs `management`+ |
| POST | `/incidents/{id}/timeline` | any | Append a comment (actor is taken from your login) |
| DELETE | `/incidents/{id}` | admin | Delete an incident (and its attachments) |
| POST | `/incidents/{id}/attachments?kind=picture\|document` | any | Upload a file (multipart/form-data, field `file`, optional `description`); max 8 MB |
| GET | `/incidents/{id}/attachments/{attachment_id}` | any | Download/view a file |
| DELETE | `/incidents/{id}/attachments/{attachment_id}` | any | Remove a file |
| GET | `/incidents/{id}/pdf` | any | Download a PDF of the incident matching the paper form's layout (see below) |
| GET | `/analytics` | any | Statistics: volume trend, breakdowns, top people, repeat patterns (see below) |

"any" means any logged-in account regardless of role.

### Example: log in and report an incident

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "ChangeMe123!"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST http://localhost:8000/incidents \
  -H "Authorization: Bearer $TOKEN" \
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
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@dock7.jpg" \
  -F "description=Dock door 7, wide angle"
```

## PDF export

`GET /incidents/{id}/pdf` renders a stored incident back into a PDF using
the paper form's own section numbers and titles ("SECTION 1: REPORTING
DETAILS", …, "SECTION 12: APPROVALS") — useful for printing, emailing, or
filing with authorities/insurance.

```bash
curl -H "Authorization: Bearer $TOKEN" "http://localhost:8000/incidents/<id>/pdf" -o report.pdf
```

- Up to 6 incident pictures are embedded directly (not just listed by name); any beyond that, or non-image supporting documents, are listed by filename
- A sign-off's drawn signature image is embedded if present, otherwise its typed signature text is shown
- A corrupted or unusual image upload is skipped rather than failing the whole PDF — ReportLab defers image decoding until the page is actually drawn, well past any `try`/`except` around building the page, so `app/pdf_export.py` fully decodes every image via Pillow up front
- The web UI's incident detail view has a "Download PDF" button (fetched with your auth header, since a plain link can't carry one — same pattern as viewing an attachment)

## Analytics

`GET /analytics` answers the questions a paper log can't: is the same
person or the same kind of incident showing up repeatedly, and how does
volume trend over time?

Query params: `period` (`month` \| `quarter` \| `year`, default `month`) controls
how the trend is bucketed; `from_date` / `to_date` (`YYYY-MM-DD`, inclusive)
filter by `incident_date`; `top_n` (default 10) caps the ranked lists.

```bash
curl "http://localhost:8000/analytics?period=quarter&from_date=2026-01-01"
```

Returns:
- `total_incidents`, `status_breakdown`, `nature_breakdown`, `category_breakdown`
- `trend`: incident counts bucketed by month/quarter/year
- `top_sites`, `top_reporters`, `top_reviewers`, `top_approvers`: ranked counts
- `repeat_involved_persons`: anyone named in Section 2 of more than one
  incident (matched by name + ID/labour card when available), with a count
  and the date they last appeared
- `repeat_site_category_patterns`: `(site, incident type)` pairs that
  recur more than once — a flag for "this keeps happening at this location"

This is computed in Python over a lean field projection rather than a
MongoDB aggregation pipeline, since incident dates are stored as bare ISO
strings (see `app/routers/incidents.py`'s `_json_safe`) — see
`app/routers/analytics.py` for the tradeoff and when to revisit it.
The web UI's **Analytics** tab renders all of this as stat cards, a trend
chart, ranked bar lists, and two tables highlighting repeat people and
repeat patterns.

## Roadmap

Incident reporting + storage (matching the paper form), file attachments,
drawn signature capture, a statistics/analytics layer, role-based
authentication, PDF export, a Docker-based local setup, and a web UI are
done. Natural next steps:

- **Reusable per-person signature library** — now that real accounts exist, save a signature once and offer it for reuse on future sign-offs instead of drawing it fresh every time
- Refresh tokens / session revocation (currently a session just expires after `ACCESS_TOKEN_EXPIRE_MINUTES` and needs a fresh login)
- Notifications/webhooks on new reports or status changes
- Additional platform modules (patrol logs, access control, asset inventory) sharing the same database

## Notes on the database driver

The API uses [Motor](https://motor.readthedocs.io/) for async MongoDB
access. MongoDB has announced Motor's deprecation in favor of PyMongo's
native async driver (`pymongo.AsyncMongoClient`, available since PyMongo
4.9). Motor remains fully functional today and has the most mature
ecosystem for testing (`mongomock-motor`), so it's the pragmatic choice
for this milestone — but migrating `app/database.py` to PyMongo's native
async client is worth revisiting later.
