"""Pydantic models for accounts and authentication.

Kept separate from models.py (the incident-report domain) since this is a
different concern: who is allowed to use the platform, and what they're
allowed to do once logged in.
"""

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, EmailStr, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Role(str, Enum):
    """Mirrors the paper form's own sign-off hierarchy (Section 12):
    Security Officer prepares a report, Security Supervisor reviews it,
    Management approves it. ADMIN is a platform-only role for account
    management and always satisfies any role requirement below it."""

    SECURITY_OFFICER = "security_officer"
    SECURITY_SUPERVISOR = "security_supervisor"
    MANAGEMENT = "management"
    ADMIN = "admin"


# Higher number = more senior. Used for "this action needs role X or
# above" checks (e.g. only a Supervisor+ may sign reviewed_by).
ROLE_LEVEL: dict[Role, int] = {
    Role.SECURITY_OFFICER: 1,
    Role.SECURITY_SUPERVISOR: 2,
    Role.MANAGEMENT: 3,
    Role.ADMIN: 4,
}


class UserCreate(BaseModel):
    """Admin-only: provisioning a new account. There is no public
    self-registration — see the Roadmap for why."""

    email: EmailStr
    full_name: str = Field(..., min_length=1, max_length=200)
    role: Role
    password: str = Field(..., min_length=8, max_length=200)


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    password: str | None = Field(default=None, min_length=8, max_length=200)


class UserPublic(BaseModel):
    """Never includes hashed_password."""

    id: str
    email: EmailStr
    full_name: str
    role: Role
    is_active: bool
    created_at: datetime
    saved_signature_image: str | None = Field(
        default=None,
        description="This person's reusable signature (base64 PNG data URI), set via PUT /auth/me/signature.",
    )
    saved_signature_text: str | None = Field(default=None, max_length=200, description="Reusable typed signature.")


class SignatureUpdate(BaseModel):
    """Save or replace the caller's own reusable signature — self-service
    only, there's no endpoint for setting someone else's. Send both fields
    null to clear it. Kept separate from UserUpdate since this is something
    any logged-in person manages for themselves, not an admin/management
    action on someone else's account."""

    signature_image: str | None = Field(
        default=None,
        max_length=300_000,
        description="Hand-drawn signature as a base64 PNG data URI (data:image/png;base64,...).",
    )
    signature: str | None = Field(default=None, max_length=200, description="Typed signature")


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserPublic


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    """Response for POST /auth/refresh — only a new access token; the
    refresh token itself isn't rotated (see README's Authentication &
    roles section for why), so the client keeps using the one it has."""

    access_token: str
    token_type: str = "bearer"


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(..., min_length=8, max_length=200)


class CreatedBy(BaseModel):
    """Who actually filed this record through the system — distinct from
    the paper form's free-text 'Reported By' field, which the reporting
    officer fills in and which may name someone else (e.g. a supervisor
    logging an incident called in by a guard)."""

    id: str
    name: str
