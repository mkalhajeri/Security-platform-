"""Pydantic models for the managed Sites registry.

A "site" is one of the organization's own named facilities (a warehouse, a
yard, a gate...) — the higher-level place an incident happened at. It is
kept separate from the incident's own free-text `exact_location` field
(Section 3 of the paper form), which describes where *within* the chosen
site the incident occurred.

Sites are admin-managed (mirrors the Users registry in auth_models.py /
routers/users.py) so the list a reporting officer picks from stays clean —
and so each site's short `code` can seed that site's incident numbers (see
app/numbering.py) without depending on free-text spelling.
"""

from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SiteCreate(BaseModel):
    code: str = Field(
        ...,
        min_length=2,
        max_length=10,
        description="Short code seeding this site's incident numbers, e.g. 'JAW' for Jebel Ali Warehouse.",
    )
    name: str = Field(..., min_length=1, max_length=200)

    @field_validator("code")
    @classmethod
    def _normalize_code(cls, value: str) -> str:
        value = value.strip().upper()
        if not value.isalnum():
            raise ValueError("Site code must contain only letters and numbers.")
        return value


class SiteUpdate(BaseModel):
    """Only the display name and active flag can change after creation —
    the code is immutable once set, since it has likely already seeded
    real incident numbers and changing it would make those numbers
    misleading."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    is_active: bool | None = None


class SitePublic(BaseModel):
    id: str
    code: str
    name: str
    is_active: bool
    created_at: datetime
