"""Pydantic models for physical security incident reports.

These mirror the organization's paper "Security Incident Report" form
section-for-section, so every field here maps to a labeled box on that
form. They double as the schema documentation for the `incidents`
MongoDB collection.
"""

from datetime import date, datetime, time, timezone
from enum import Enum

from pydantic import BaseModel, Field

from app.auth_models import CreatedBy


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Enums (each maps to a checkbox group or dropdown on the paper form)
# ---------------------------------------------------------------------------


class Status(str, Enum):
    """Digital workflow state — not on the paper form, but needed to track
    a report from filing through sign-off."""

    REPORTED = "reported"
    UNDER_REVIEW = "under_review"
    CLOSED = "closed"


class Gender(str, Enum):
    M = "M"
    F = "F"


class ReportedVia(str, Enum):
    EMAIL = "email"
    PHONE_CALL = "phone_call"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    OTHER = "other"


class NatureOfReport(str, Enum):
    INCIDENT = "incident"
    ACCIDENT = "accident"
    NEAR_MISS = "near_miss"
    SECURITY = "security"
    OTHER = "other"


class IncidentCategory(str, Enum):
    """Section 4: Type of Incident (select all that apply)."""

    VEHICLE_ACCIDENT = "vehicle_accident"
    INJURY = "injury"
    ILLNESS_MEDICAL = "illness_medical"
    FIRE_EXPLOSION = "fire_explosion"
    THEFT = "theft"
    ATTEMPTED_THEFT = "attempted_theft"
    VANDALISM_DAMAGE = "vandalism_damage"
    PROPERTY_DAMAGE = "property_damage"
    SECURITY_BREACH = "security_breach"
    HARASSMENT = "harassment"
    VERBAL_ABUSE = "verbal_abuse"
    PHYSICAL_ASSAULT = "physical_assault"
    DRUG_ALCOHOL = "drug_alcohol"
    TRAFFIC_VIOLATION = "traffic_violation"
    ENVIRONMENTAL = "environmental"
    FALL_FROM_HEIGHT = "fall_from_height"
    ELECTRICAL = "electrical"
    CHEMICAL_SPILL = "chemical_spill"
    EQUIPMENT_FAILURE = "equipment_failure"
    OTHER = "other"


class SupportingDocumentType(str, Enum):
    """Section 11: Supporting Documents."""

    PHOTOS = "photos"
    CCTV_FOOTAGE = "cctv_footage"
    MEDICAL_REPORT = "medical_report"
    WITNESS_STATEMENT = "witness_statement"
    POLICE_REPORT = "police_report"
    VEHICLE_REPORT = "vehicle_report"
    MAINTENANCE_REPORT = "maintenance_report"
    OTHER = "other"


class AttachmentKind(str, Enum):
    PICTURE = "picture"  # Section 9: Incident Pictures
    DOCUMENT = "document"  # Section 11: Supporting Documents


# ---------------------------------------------------------------------------
# Sub-objects
# ---------------------------------------------------------------------------


class InvolvedPerson(BaseModel):
    """Section 2: Involved Person(s) table — one row."""

    name: str = Field(..., min_length=1, max_length=200)
    designation: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    id_number: str | None = Field(default=None, max_length=100, description="ID No. / Labour Card")
    nationality: str | None = Field(default=None, max_length=100)
    contact_no: str | None = Field(default=None, max_length=50)
    gender: Gender | None = None


class ApprovalSignOff(BaseModel):
    """Section 12: one Approvals column (Prepared By / Reviewed By / Approved By).

    Field named `signed_date`, not `date` — naming it `date` would shadow
    the imported `datetime.date` type within its own annotation.
    """

    name: str | None = Field(default=None, max_length=200)
    position: str | None = Field(default=None, max_length=200)
    signed_date: date | None = None
    signature: str | None = Field(default=None, max_length=200, description="Typed signature")
    signature_image: str | None = Field(
        default=None,
        max_length=300_000,
        description=(
            "Hand-drawn signature as a base64 PNG data URI (data:image/png;base64,...). "
            "Not yet tied to a reusable per-person signature library — that needs real "
            "user accounts first — so each sign-off is drawn fresh."
        ),
    )


class TimelineEntry(BaseModel):
    """A single event/comment appended to an incident's history."""

    timestamp: datetime = Field(default_factory=utcnow)
    actor: str = Field(..., description="Name or identifier of who made this update")
    action: str = Field(..., description="Short label, e.g. 'status_change', 'comment'")
    note: str | None = None


class TimelineEntryCreate(BaseModel):
    """`actor` is not client-supplied — the endpoint fills it in from the
    authenticated user, so someone can't post a comment "as" someone else."""

    action: str = Field(..., min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=5000)


class AttachmentMeta(BaseModel):
    """Metadata for a stored file. The bytes live in the `attachments`
    collection, keyed by `id`; this is what gets embedded on the incident."""

    id: str
    filename: str
    content_type: str
    size: int
    description: str | None = None
    uploaded_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# Incident: create / update / response
# ---------------------------------------------------------------------------


class IncidentCreate(BaseModel):
    # Section 1: Reporting Details
    #
    # "Site" is the organization's own named facility (e.g. a warehouse or
    # yard) — chosen from the managed Sites registry (see app/site_models.py)
    # so incident numbers and per-site analytics stay accurate. It is
    # distinct from `exact_location` below, which describes where *within*
    # that site the incident happened. Pick one: `site_id` references a
    # registered site; `site_other` is free text for a site not in the
    # list. The server resolves whichever is given into the stored
    # `site_location`/`site_code` (see `_resolve_site` in routers/incidents.py).
    site_id: str | None = Field(default=None, description="Registered Site's id; omit and use site_other instead")
    site_other: str | None = Field(
        default=None, max_length=200, description="Free-text site name, required when site_id is omitted"
    )
    department_area: str | None = Field(default=None, max_length=200)
    report_date: date
    report_time: time
    reported_by: str = Field(..., min_length=1, max_length=200)
    reported_by_job_title: str | None = Field(default=None, max_length=200)
    reported_by_id_no: str | None = Field(default=None, max_length=100)
    reported_via: ReportedVia
    reported_via_other: str | None = Field(default=None, max_length=200)
    nature_of_report: NatureOfReport
    nature_of_report_other: str | None = Field(default=None, max_length=200)

    # Section 2: Involved Person(s)
    involved_persons: list[InvolvedPerson] = Field(default_factory=list)
    witnesses: str | None = Field(default=None, max_length=2000)

    # Section 3: Incident Details
    incident_type_summary: str | None = Field(default=None, max_length=300)
    exact_location: str = Field(..., min_length=1, max_length=300)
    incident_date: date
    incident_time: time

    # Section 4: Type of Incident
    incident_categories: list[IncidentCategory] = Field(default_factory=list)
    incident_category_other: str | None = Field(default=None, max_length=200)

    # Sections 5-8, 10: narrative fields
    incident_background: str = Field(..., min_length=1, max_length=10000)
    immediate_action_taken: str | None = Field(default=None, max_length=10000)
    root_cause: str | None = Field(default=None, max_length=5000)
    recommendations: str | None = Field(default=None, max_length=5000)
    local_authorities_involvement: str | None = Field(default=None, max_length=5000)

    # Section 11: Supporting Documents (checkboxes; files uploaded separately)
    supporting_documents: list[SupportingDocumentType] = Field(default_factory=list)
    supporting_documents_other: str | None = Field(default=None, max_length=200)

    # Section 12: Approvals — usually only "prepared by" is known at filing time
    prepared_by: ApprovalSignOff | None = None


class IncidentUpdate(BaseModel):
    """All fields optional — only provided fields are changed."""

    site_id: str | None = None
    site_other: str | None = Field(default=None, max_length=200)
    department_area: str | None = Field(default=None, max_length=200)
    report_date: date | None = None
    report_time: time | None = None
    reported_by: str | None = Field(default=None, min_length=1, max_length=200)
    reported_by_job_title: str | None = Field(default=None, max_length=200)
    reported_by_id_no: str | None = Field(default=None, max_length=100)
    reported_via: ReportedVia | None = None
    reported_via_other: str | None = Field(default=None, max_length=200)
    nature_of_report: NatureOfReport | None = None
    nature_of_report_other: str | None = Field(default=None, max_length=200)

    involved_persons: list[InvolvedPerson] | None = None
    witnesses: str | None = Field(default=None, max_length=2000)

    incident_type_summary: str | None = Field(default=None, max_length=300)
    exact_location: str | None = Field(default=None, min_length=1, max_length=300)
    incident_date: date | None = None
    incident_time: time | None = None

    incident_categories: list[IncidentCategory] | None = None
    incident_category_other: str | None = Field(default=None, max_length=200)

    incident_background: str | None = Field(default=None, min_length=1, max_length=10000)
    immediate_action_taken: str | None = Field(default=None, max_length=10000)
    root_cause: str | None = Field(default=None, max_length=5000)
    recommendations: str | None = Field(default=None, max_length=5000)
    local_authorities_involvement: str | None = Field(default=None, max_length=5000)

    supporting_documents: list[SupportingDocumentType] | None = None
    supporting_documents_other: str | None = Field(default=None, max_length=200)

    status: Status | None = None
    prepared_by: ApprovalSignOff | None = None
    reviewed_by: ApprovalSignOff | None = None
    approved_by: ApprovalSignOff | None = None


class IncidentResponse(BaseModel):
    id: str
    status: Status

    incident_number: str = Field(
        ..., description="Site + year document number, e.g. 'JAW-2026-0001' (assigned once, at creation)."
    )
    site_id: str | None = Field(default=None, description="Registered Site's id, or null if filed under 'Other'.")
    site_code: str = Field(..., description="Numbering prefix used for this incident's site (e.g. 'JAW', or 'OTH').")
    site_location: str
    department_area: str | None = None
    report_date: date
    report_time: time
    reported_by: str
    reported_by_job_title: str | None = None
    reported_by_id_no: str | None = None
    reported_via: ReportedVia
    reported_via_other: str | None = None
    nature_of_report: NatureOfReport
    nature_of_report_other: str | None = None

    involved_persons: list[InvolvedPerson] = Field(default_factory=list)
    witnesses: str | None = None

    incident_type_summary: str | None = None
    exact_location: str
    incident_date: date
    incident_time: time

    incident_categories: list[IncidentCategory] = Field(default_factory=list)
    incident_category_other: str | None = None

    incident_background: str
    immediate_action_taken: str | None = None
    root_cause: str | None = None
    recommendations: str | None = None
    local_authorities_involvement: str | None = None

    incident_pictures: list[AttachmentMeta] = Field(default_factory=list)
    supporting_documents: list[SupportingDocumentType] = Field(default_factory=list)
    supporting_documents_other: str | None = None
    supporting_document_files: list[AttachmentMeta] = Field(default_factory=list)

    prepared_by: ApprovalSignOff | None = None
    reviewed_by: ApprovalSignOff | None = None
    approved_by: ApprovalSignOff | None = None

    timeline: list[TimelineEntry] = Field(default_factory=list)
    created_by: CreatedBy | None = None
    created_at: datetime
    updated_at: datetime


class IncidentListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[IncidentResponse]


def incident_to_response(doc: dict) -> dict:
    """Convert a raw MongoDB document into a dict matching IncidentResponse."""

    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return doc
