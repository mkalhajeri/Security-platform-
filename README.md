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
    auth.py           POST /auth/login, GET /auth/me, POST /auth/change-password, PUT /auth/me/signature
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
| System | `id`, `status`, `incident_number` | `status` is `reported` \| `under_review` \| `closed` — a digital workflow state not on the paper form. `incident_number` (e.g. `JAW-2026-0001`) is a site+year document number assigned once at creation — see [Sites & incident numbering](#sites--incident-numbering) |
| 1. Reporting Details | `site_id`, `site_code`, `site_location`, `department_area`, `report_date`, `report_time`, `reported_by`, `reported_by_job_title`, `reported_by_id_no`, `reported_via` (`email`\|`phone_call`\|`sms`\|`whatsapp`\|`other`), `nature_of_report` (`incident`\|`accident`\|`near_miss`\|`security`\|`other`) | `site_id`/`site_code`/`site_location` are resolved server-side from the reporter's site choice — see below. `site_location` remains the display name shown throughout the UI, PDF, and analytics |
| 2. Involved Person(s) | `involved_persons`: list of `{name, designation, company, id_number, nationality, contact_no, gender}`; `witnesses` (free text) | Repeatable rows |
| 3. Incident Details | `incident_type_summary`, `exact_location`, `incident_date`, `incident_time` | |
| 4. Type of Incident | `incident_categories`: list of 20 categories (vehicle accident, injury, fire/explosion, theft, security breach, physical assault, …) + `incident_category_other` | Multi-select |
| 5–8, 10. Narrative | `incident_background`, `immediate_action_taken`, `root_cause`, `recommendations`, `local_authorities_involvement` | Free text |
| 9. Incident Pictures | `incident_pictures`: list of attachment metadata `{id, filename, content_type, size, description, uploaded_at}` | Files uploaded separately, see below |
| 11. Supporting Documents | `supporting_documents`: list of 8 document types (photos, CCTV footage, police report, …) + `supporting_documents_other`; `supporting_document_files`: uploaded file metadata | |
| 12. Approvals | `prepared_by`, `reviewed_by`, `approved_by`: each `{name, position, signed_date, signature, signature_image}` | `signature` is typed text; `signature_image` is a hand-drawn signature captured on a canvas pad, stored as a base64 PNG data URI (max 300 KB), specific to that one sign-off. For `reviewed_by`/`approved_by`, `name`/`position` are always server-set from the signer's account — see [Authentication & roles](#authentication--roles). Independent of the signer's own *reusable* saved signature (`UserPublic.saved_signature_image`, see below) — the signature pad offers that as a starting point, but each sign-off still stores its own copy. |
| — | `created_by`: `{id, name}` | Who actually filed the record through the system (from their login) — distinct from the free-text `reported_by` paper-form field, which may name someone else (e.g. a supervisor logging what a guard called in) |
| — | `timeline`: list of `{timestamp, actor, action, note}` | `actor` comes from the logged-in user, not client input. Auto-appended on creation, status changes, sign-offs, and attachment changes; can also be appended to manually |
| — | `created_at` / `updated_at` (UTC) | Managed by the API |

## Sites & incident numbering

A **site** is one of the organization's own named facilities (a warehouse,
a yard, a gate...) — the higher-level place an incident happened at. It's
kept separate from the incident's own `exact_location` field (Section 3),
which describes where *within* the site it occurred.

Sites are a small managed registry (`GET/POST/PATCH/DELETE /sites`,
mirroring the Users registry), each with a short `code` (e.g. `JAW`) and a
display `name` (e.g. `Jebel Ali Warehouse`). Any logged-in user can list
sites (the New Report form needs the active list for its dropdown); an
**Admin or Management-level** user can add, rename, deactivate, or delete
one — see [Adding sites manually](#adding-sites-manually) below. A site's
`code` is immutable once set, since it seeds every incident number filed
against it.

When filing a report, the reporting officer either **picks a registered
site** from the dropdown, or **chooses "Other"** and types a one-off site
name — both are accepted by `POST /incidents` as `site_id` (a registered
site's id) or `site_other` (free text), never both. The server resolves
whichever was given into `site_id` (`null` for "Other"), `site_code`, and
`site_location` on the stored incident.

That `site_code` plus the incident's year seeds its **incident number** —
`{SITE_CODE}-{YEAR}-{SEQUENCE}`, e.g. `JAW-2026-0001` — assigned once, at
creation, and never changed afterward even if the site is edited later.
The sequence resets every calendar year and is scoped per site, generated
atomically (a `$inc` against a dedicated `counters` collection) so two
reports filed for the same site in the same instant can't collide.
Incidents filed under "Other" all share a single `OTH` numbering prefix
rather than inventing a code from free text.

The number is searchable/filterable: `GET /incidents?incident_number=JAW-2026`
matches by exact value or leading prefix. `GET /incidents?site_id=<id>`
filters to one registered site. Because `site_location` is now either a
managed site's consistent display name or a genuinely one-off "Other"
entry, the Analytics `top_sites` breakdown (grouped by `site_location`)
gives accurate per-site counts without being fragmented by typos or
spelling variants the way free-text entry used to allow.

A registered site with existing incidents can't be deleted (`DELETE
/sites/{id}` returns 409) — deactivate it instead (`PATCH /sites/{id}`
with `{"is_active": false}`), which hides it from the New Report dropdown
while keeping every past incident's site reference intact.

### Adding sites manually

Covering a new site isn't an AI/backend job — it's a normal admin task an
Admin or Management-level user does directly, in the web UI or the API,
whenever a new location needs to be reportable:

1. Log in as an Admin or Management-level account and open the **Sites**
   tab.
2. Fill in **New site**: a short `code` (2–10 letters/numbers, e.g. `DXB2`
   — this seeds that site's incident numbers, so pick something readable
   and permanent) and a display `name` (e.g. "Dubai Yard 2").
3. Click **Add site** — it's immediately available in every reporting
   officer's New Report site dropdown, no restart or deploy involved.
4. To retire a site later without losing its history, toggle it inactive
   in the same tab (it disappears from the dropdown but every past
   incident filed against it is untouched); a site with zero incidents can
   be deleted outright.

The same thing via the API:

```bash
curl -X POST http://localhost:8000/sites \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"code": "DXB2", "name": "Dubai Yard 2"}'
```

## Authentication & roles

There is **no public sign-up**. This is an internal tool for a specific
security department, so accounts are provisioned manually by an Admin or a
Management-level user for named staff, and every `/incidents` and
`/analytics` endpoint requires a logged-in account.

**Roles**, matching the paper form's own Section 12 sign-off hierarchy
(each level also has everything the levels below it have):

| Role | Can do |
|---|---|
| `security_officer` | File and view incident reports; prepare (Section 12 "Prepared By") |
| `security_supervisor` | + Sign off as "Reviewed By" |
| `management` | + Sign off as "Approved By"; create/edit/deactivate/delete Security Officer and Security Supervisor accounts; create/edit/deactivate/delete sites |
| `admin` | + Delete incidents; create/edit/deactivate/delete **any** account (including other Management/Admin accounts) |

A sign-off's name and position always come from **whoever is logged in**,
never from the request body — a Supervisor can't sign a review "as" someone
else, and the server rejects the attempt at the role check before it ever
looks at the name field.

**Account management is capped at your own level.** A Management-level
user can provision and manage Security Officer / Security Supervisor
accounts, but can never create, edit, or delete a peer Management account
or an Admin account — nor assign the `management` or `admin` role to
anyone, including themselves. That ceiling is enforced server-side
(`app/routers/users.py`'s `_assert_can_manage_role`), not just hidden in
the UI, so it holds even against a direct API call. Only an Admin can
manage another Management or Admin account, or grant either role.

**Bootstrap admin:** on first startup, if no admin account exists yet, one
is created automatically from `ADMIN_EMAIL` / `ADMIN_PASSWORD` /
`ADMIN_FULL_NAME` (see `.env.example`). Log in and change that password
immediately in any deployment beyond your own machine — the app logs a
warning on startup if you're still using the default.

**Session tokens:** login returns two JWTs — an **access token**
(`Authorization: Bearer <token>` header on every request), short-lived by
design (`ACCESS_TOKEN_EXPIRE_MINUTES`, default 30 minutes), and a
**refresh token** (`REFRESH_TOKEN_EXPIRE_DAYS`, default 14 days), sent only
to `POST /auth/refresh` to get a new access token without re-entering a
password. The web UI does this automatically the moment a request comes
back 401 — in normal use a session just keeps working for as long as the
refresh token is valid, without the 12-hour-token feel of earlier versions
of this app.

**Revoking a session before it expires:** neither token is looked up in a
database per request — that's the point of a JWT — so revocation instead
works through a `token_version` counter on the account, embedded in every
token it issues. `POST /auth/logout-everywhere` (any user, on their own
account) and `POST /users/{id}/revoke-sessions` (Admin/Management, same
hierarchy ceiling as editing/deleting an account — see above) both bump
it, which instantly invalidates every access/refresh token issued
before that moment. `POST /auth/change-password` bumps it too — a changed
password logs out every *other* session — but hands the caller a fresh
token pair in its response so their own session keeps working. This is
deliberately all-or-nothing (it can't revoke just one device while leaving
others logged in) — the pragmatic tradeoff of a single counter over a full
per-device session store, matching the scale of an internal
department tool; deactivating an account remains the immediate,
unconditional kill switch regardless of any of this.

**Reusable signature:** `PUT /auth/me/signature` (any logged-in user, on
their own account only) saves a drawn or typed signature — `{signature_image,
signature}`, same shape and 300 KB limit as a sign-off's own signature
fields — and returns it on every `GET /auth/me`/`login` response afterward
as `saved_signature_image`/`saved_signature_text`. The web UI's signature
pad (used for Prepared By / Reviewed By / Approved By) draws it in as a
starting point automatically whenever that particular sign-off doesn't
already have its own image, with a "Use my saved signature" button to pull
it in on demand and a checkbox to save whatever's currently drawn back to
the account. Send both fields `null` to clear it. This is separate from any
individual sign-off's stored `signature_image` — saving a new one here
doesn't retroactively change past incidents.

### Adding user accounts manually

Same principle as sites: bringing on a new staff member is a normal manual
admin task, not something that depends on AI involvement. An Admin or
Management-level user does it directly, in the web UI or the API:

1. Log in as an Admin or Management-level account and open the **Users**
   tab.
2. Fill in **New account**: full name, email (this is their login), a
   role, and a temporary password (8+ characters). A Management-level user
   only sees `security_officer` and `security_supervisor` as assignable
   roles; an Admin sees all four.
3. Click **Create account** — hand the new person their email and
   temporary password through whatever channel your department already
   uses for that (this app has no email-sending of its own).
4. The new person logs in with those credentials, then should change the
   password immediately via **Log in → `/auth/change-password`** (the web
   UI doesn't yet expose this as its own screen — call it directly, or ask
   an Admin/Management user to set a fresh password for them via the Users
   tab).
5. To offboard someone, deactivate their account (unchecking "Active" in
   the Users tab, or `PATCH /users/{id}` with `{"is_active": false}`) —
   their login stops working immediately but their name stays intact on
   every incident/timeline entry/sign-off they're attached to. Delete only
   if the account should never come back. **Force logout** (in the same
   row) ends every session they're currently signed in on without
   deactivating the account — useful right after a role change, or if a
   device of theirs was lost, without locking them out entirely.

The same thing via the API:

```bash
curl -X POST http://localhost:8000/users \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"email": "new.officer@example.com", "full_name": "New Officer", "role": "security_officer", "password": "TempPass123!"}'
```

## API endpoints

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/` | — | Web UI (static SPA) |
| GET | `/api/info` | — | API name/version metadata |
| GET | `/health` | — | Liveness/readiness check (pings MongoDB) |
| POST | `/auth/login` | — | Log in with `{email, password}`, returns an access token + refresh token + user profile |
| POST | `/auth/refresh` | — | Exchange `{refresh_token}` for a new access token |
| GET | `/auth/me` | any | Current user's own profile |
| POST | `/auth/change-password` | any | Change your own password (`{current_password, new_password}`); returns a fresh token pair and revokes every other session |
| POST | `/auth/logout-everywhere` | any | Revoke every access/refresh token issued to your own account so far |
| PUT | `/auth/me/signature` | any | Save/replace/clear your own reusable signature (`{signature_image, signature}`) |
| POST | `/users` | management+ | Create an account (`{email, full_name, role, password}`). Management can only assign `security_officer`/`security_supervisor` |
| GET | `/users` | management+ | List all accounts |
| PATCH | `/users/{id}` | management+ | Update name/role/active-status/password. Management can only touch, or assign, `security_officer`/`security_supervisor` accounts — never a peer Management or an Admin account |
| DELETE | `/users/{id}` | management+ | Delete an account (same Management ceiling as above) |
| POST | `/users/{id}/revoke-sessions` | management+ | Force that account to need a fresh login everywhere, without deactivating it (same Management ceiling as above) |
| POST | `/sites` | management+ | Register a site (`{code, name}`) |
| GET | `/sites` | any | List sites (active only by default; `?include_inactive=true` for all) |
| PATCH | `/sites/{id}` | management+ | Rename or activate/deactivate a site |
| DELETE | `/sites/{id}` | management+ | Delete a site (only if no incident references it — deactivate otherwise) |
| POST | `/incidents` | any | Report a new incident. Site is `site_id` (a registered site) or `site_other` (free text); the server assigns `incident_number` |
| GET | `/incidents` | any | List incidents (filter by `status`, `nature_of_report`, `category`, `site_location`, `site_id`, `incident_number`, `search`; paginate with `limit`/`offset`) |
| GET | `/incidents/{id}` | any | Get one incident |
| PATCH | `/incidents/{id}` | any\* | Update fields (partial); status changes and new sign-offs are recorded in the timeline. \*Setting `reviewed_by` needs `security_supervisor`+, `approved_by` needs `management`+ |
| POST | `/incidents/{id}/timeline` | any | Append a comment (actor is taken from your login) |
| DELETE | `/incidents/{id}` | admin | Delete an incident (and its attachments) |
| POST | `/incidents/{id}/attachments?kind=picture\|document` | any | Upload a file (multipart/form-data, field `file`, optional `description`); max 8 MB |
| GET | `/incidents/{id}/attachments/{attachment_id}` | any | Download/view a file |
| DELETE | `/incidents/{id}/attachments/{attachment_id}` | any | Remove a file |
| GET | `/incidents/{id}/pdf` | any | Download a PDF of the incident matching the paper form's layout (see below) |
| GET | `/analytics` | any | Statistics: volume trend, breakdowns, top people, repeat patterns (see below) |

"any" means any logged-in account regardless of role. "management+" means
Management or Admin (Admin unrestricted; Management capped as described
in [Authentication & roles](#authentication--roles)).

### Example: log in and report an incident

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "admin@example.com", "password": "ChangeMe123!"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

curl -X POST http://localhost:8000/incidents \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
        "site_other": "Warehouse 3 — Jebel Ali",
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
filing with authorities/insurance. The incident number appears in the
document title, the header subtitle, and Section 1.

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
drawn signature capture, a reusable per-person signature library, a
statistics/analytics layer, role-based authentication (including
Management-level account/site provisioning, refresh tokens, and session
revocation), site registry + incident numbering, PDF export, a
Docker-based local setup, and a web UI are done. Natural next steps:

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
