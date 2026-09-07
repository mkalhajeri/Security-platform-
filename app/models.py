"""Pydantic models for security incidents.

These define the shape of data accepted by and returned from the API, and
double as the schema documentation for the `incidents` MongoDB collection.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class Status(str, Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    CONTAINED = "contained"
    RESOLVED = "resolved"
    CLOSED = "closed"


class Category(str, Enum):
    MALWARE = "malware"
    PHISHING = "phishing"
    DATA_BREACH = "data_breach"
    UNAUTHORIZED_ACCESS = "unauthorized_access"
    DENIAL_OF_SERVICE = "denial_of_service"
    INSIDER_THREAT = "insider_threat"
    VULNERABILITY = "vulnerability"
    POLICY_VIOLATION = "policy_violation"
    OTHER = "other"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimelineEntry(BaseModel):
    """A single event/comment appended to an incident's history."""

    timestamp: datetime = Field(default_factory=utcnow)
    actor: str = Field(..., description="Name or identifier of who made this update")
    action: str = Field(..., description="Short label, e.g. 'status_change', 'comment'")
    note: str | None = None


class TimelineEntryCreate(BaseModel):
    actor: str = Field(..., min_length=1, max_length=200)
    action: str = Field(..., min_length=1, max_length=100)
    note: str | None = Field(default=None, max_length=5000)


class IncidentCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=300)
    description: str = Field(..., min_length=1, max_length=10000)
    severity: Severity = Severity.MEDIUM
    category: Category = Category.OTHER
    reporter_name: str = Field(..., min_length=1, max_length=200)
    reporter_email: EmailStr | None = None
    affected_systems: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class IncidentUpdate(BaseModel):
    """All fields optional — only provided fields are changed."""

    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, min_length=1, max_length=10000)
    severity: Severity | None = None
    status: Status | None = None
    category: Category | None = None
    affected_systems: list[str] | None = None
    tags: list[str] | None = None


class IncidentResponse(BaseModel):
    id: str
    title: str
    description: str
    severity: Severity
    status: Status
    category: Category
    reporter_name: str
    reporter_email: EmailStr | None = None
    affected_systems: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    timeline: list[TimelineEntry] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None = None


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
