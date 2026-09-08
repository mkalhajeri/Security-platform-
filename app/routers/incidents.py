"""CRUD + attachment endpoints for physical security incident reports."""

import re
from datetime import date, time
from enum import Enum

from bson import ObjectId
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth import get_current_user, require_min_role
from app.auth_models import ROLE_LEVEL, Role, UserPublic
from app.database import get_database
from app.numbering import OTHER_SITE_CODE, next_incident_number
from app.pdf_export import build_incident_pdf
from app.models import (
    AttachmentKind,
    AttachmentMeta,
    IncidentCategory,
    IncidentCreate,
    IncidentListResponse,
    IncidentResponse,
    IncidentUpdate,
    NatureOfReport,
    Status,
    TimelineEntry,
    TimelineEntryCreate,
    incident_to_response,
    utcnow,
)

# Every incident endpoint requires a logged-in security-department account;
# individual endpoints layer on a minimum role where the paper form's own
# sign-off hierarchy calls for one (see update_incident).
router = APIRouter(prefix="/incidents", tags=["incidents"], dependencies=[Depends(get_current_user)])

# Section 12 sign-off fields, each gated at the paper form's own minimum
# role: a Security Officer can prepare a report, but only a Supervisor+ may
# review it and only Management+ may approve it.
_SIGNOFF_MIN_ROLE = {
    "reviewed_by": Role.SECURITY_SUPERVISOR,
    "approved_by": Role.MANAGEMENT,
}

# Kept well under MongoDB's 16 MiB document limit, with headroom for the
# rest of the attachment document and BSON overhead.
MAX_ATTACHMENT_SIZE = 8 * 1024 * 1024


def _object_id_or_404(incident_id: str, detail: str = "Incident not found") -> ObjectId:
    if not ObjectId.is_valid(incident_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return ObjectId(incident_id)


async def _get_incident_or_404(db: AsyncIOMotorDatabase, incident_id: str) -> dict:
    oid = _object_id_or_404(incident_id)
    doc = await db["incidents"].find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return doc


async def _resolve_site(db: AsyncIOMotorDatabase, site_id: str | None, site_other: str | None) -> dict:
    """Resolve a reporter's site choice into what actually gets stored on
    the incident: `site_location` (display name), `site_code` (incident
    numbering prefix), and `site_id` (None for a one-off "Other" site).

    A registered site (site_id given) must exist and be active — an
    inactive site was presumably retired for a reason, so new reports
    shouldn't be filed against it even if a stale client still has its id
    cached. "Other" sites all share the OTHER_SITE_CODE numbering prefix
    (see app/numbering.py) rather than deriving one from free text.
    """
    if site_id:
        if not ObjectId.is_valid(site_id):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid site.")
        site = await db["sites"].find_one({"_id": ObjectId(site_id)})
        if site is None or not site.get("is_active", True):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "Selected site is not available. Choose another, or use 'Other'."
            )
        return {"site_id": str(site["_id"]), "site_location": site["name"], "site_code": site["code"]}

    other = (site_other or "").strip()
    if not other:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Choose a site from the list, or specify one under 'Other'.")
    return {"site_id": None, "site_location": other, "site_code": OTHER_SITE_CODE}


def _json_safe(value):
    """Recursively convert date/time/Enum values to BSON-friendly types.

    BSON has no bare "date" or "time" type (only full datetime), so these
    are stored as ISO strings and parsed back by the response model.

    Every enum here (Status, Gender, ReportedVia, ...) is also a `str`
    subclass, so `isinstance(value, str)` is already true for them —
    checking `isinstance(value, Enum)` explicitly (rather than something
    like "hasattr(value, 'value') and not isinstance(value, str)") is what
    actually unwraps them to their plain string value. Leaving the raw
    member in place mostly reads fine (it IS a string), but `str(member)`
    on a str-mixed Enum prints "ClassName.MEMBER" instead of the value —
    a latent bug that stays invisible once real MongoDB round-trips the
    field back as a plain string, but bites the moment something calls
    str() on it before that round-trip happens (e.g. mongomock in tests,
    or code — like PDF export — that renders a freshly-built dict).
    """
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


@router.post("", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
async def create_incident(
    payload: IncidentCreate,
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    data = payload.model_dump()
    site_id_in = data.pop("site_id", None)
    site_other_in = data.pop("site_other", None)
    site_info = await _resolve_site(db, site_id_in, site_other_in)
    incident_number = await next_incident_number(db, site_info["site_code"], payload.incident_date.year)

    now = utcnow()
    doc = _json_safe(data)
    doc.update(
        {
            **site_info,
            "incident_number": incident_number,
            "status": Status.REPORTED.value,
            "incident_pictures": [],
            "supporting_document_files": [],
            "reviewed_by": None,
            "approved_by": None,
            "created_by": {"id": current_user.id, "name": current_user.full_name},
            "timeline": [
                TimelineEntry(
                    actor=current_user.full_name,
                    action="created",
                    note="Incident reported.",
                ).model_dump()
            ],
            "created_at": now,
            "updated_at": now,
        }
    )
    result = await db["incidents"].insert_one(doc)
    created = await db["incidents"].find_one({"_id": result.inserted_id})
    return incident_to_response(created)


@router.get("", response_model=IncidentListResponse)
async def list_incidents(
    db: AsyncIOMotorDatabase = Depends(get_database),
    status_filter: Status | None = Query(default=None, alias="status"),
    nature_of_report: NatureOfReport | None = Query(default=None),
    category: IncidentCategory | None = Query(default=None),
    site_location: str | None = Query(default=None, description="Exact match on site/location"),
    site_id: str | None = Query(default=None, description="Filter by a registered Site's id"),
    incident_number: str | None = Query(
        default=None, description="Filter by incident number — exact match, or a leading prefix (e.g. 'JAW-2026')"
    ),
    search: str | None = Query(default=None, description="Full-text search over the narrative sections"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    query: dict = {}
    if status_filter is not None:
        query["status"] = status_filter.value
    if nature_of_report is not None:
        query["nature_of_report"] = nature_of_report.value
    if category is not None:
        query["incident_categories"] = category.value
    if site_location:
        query["site_location"] = site_location
    if site_id:
        query["site_id"] = site_id
    if incident_number:
        query["incident_number"] = {"$regex": f"^{re.escape(incident_number.strip())}", "$options": "i"}
    if search:
        query["$text"] = {"$search": search}

    cursor = db["incidents"].find(query).sort("created_at", -1).skip(offset).limit(limit)
    items = [incident_to_response(doc) async for doc in cursor]
    total = await db["incidents"].count_documents(query)

    return {"total": total, "limit": limit, "offset": offset, "items": items}


@router.get("/{incident_id}", response_model=IncidentResponse)
async def get_incident(
    incident_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    doc = await _get_incident_or_404(db, incident_id)
    return incident_to_response(doc)


@router.patch("/{incident_id}", response_model=IncidentResponse)
async def update_incident(
    incident_id: str,
    payload: IncidentUpdate,
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    oid = _object_id_or_404(incident_id)
    existing = await _get_incident_or_404(db, incident_id)

    updates = _json_safe(payload.model_dump(exclude_unset=True))
    if not updates:
        return incident_to_response(existing)

    # incident_number is assigned once, at creation, and never changes —
    # but the site itself can be corrected/reassigned later. Resolve it the
    # same way create_incident does, only when the caller actually sent a
    # site_id/site_other change.
    if "site_id" in updates or "site_other" in updates:
        site_id_in = updates.pop("site_id", None)
        site_other_in = updates.pop("site_other", None)
        updates.update(await _resolve_site(db, site_id_in, site_other_in))

    # Enforce the sign-off hierarchy, and bind the signer's name/position to
    # who is actually logged in rather than trusting client-supplied text —
    # otherwise any user could "sign" a review or approval as someone else.
    # signed_date/signature/signature_image are the part the signer actually
    # controls (when they signed, and their drawn or typed signature).
    for field, min_role in _SIGNOFF_MIN_ROLE.items():
        if field not in updates or not updates[field]:
            continue
        if ROLE_LEVEL[current_user.role] < ROLE_LEVEL[min_role]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"Signing '{field.replace('_', ' ')}' requires the {min_role.value.replace('_', ' ')} role or higher.",
            )
        updates[field]["name"] = current_user.full_name
        updates[field].setdefault("position", None)

    timeline_additions = []
    now = utcnow()

    if "status" in updates and updates["status"] != existing.get("status"):
        timeline_additions.append(
            TimelineEntry(
                actor=current_user.full_name,
                action="status_change",
                note=f"Status changed from {existing.get('status')} to {updates['status']}",
            ).model_dump()
        )

    for field, label in (("reviewed_by", "Reviewed by"), ("approved_by", "Approved by")):
        if field in updates and updates[field] and not existing.get(field):
            timeline_additions.append(
                TimelineEntry(
                    actor=current_user.full_name,
                    action=field,
                    note=f"{label} {current_user.full_name}",
                ).model_dump()
            )

    updates["updated_at"] = now

    update_doc: dict = {"$set": updates}
    if timeline_additions:
        update_doc["$push"] = {"timeline": {"$each": timeline_additions}}

    await db["incidents"].update_one({"_id": oid}, update_doc)
    updated = await db["incidents"].find_one({"_id": oid})
    return incident_to_response(updated)


@router.post("/{incident_id}/timeline", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
async def add_timeline_entry(
    incident_id: str,
    entry: TimelineEntryCreate,
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    oid = _object_id_or_404(incident_id)
    await _get_incident_or_404(db, incident_id)

    new_entry = TimelineEntry(actor=current_user.full_name, **entry.model_dump())
    await db["incidents"].update_one(
        {"_id": oid},
        {
            "$push": {"timeline": new_entry.model_dump()},
            "$set": {"updated_at": utcnow()},
        },
    )
    updated = await db["incidents"].find_one({"_id": oid})
    return incident_to_response(updated)


@router.delete(
    "/{incident_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_min_role(Role.ADMIN))],
)
async def delete_incident(
    incident_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> None:
    oid = _object_id_or_404(incident_id)
    result = await db["incidents"].delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    # Attachments have no independent purpose once their incident is gone.
    await db["attachments"].delete_many({"incident_id": oid})


# ---------------------------------------------------------------------------
# Attachments (Section 9: Incident Pictures, Section 11: Supporting Documents)
# ---------------------------------------------------------------------------


@router.post(
    "/{incident_id}/attachments",
    response_model=IncidentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_attachment(
    incident_id: str,
    kind: AttachmentKind,
    file: UploadFile = File(...),
    description: str | None = Form(default=None),
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    oid = _object_id_or_404(incident_id)
    await _get_incident_or_404(db, incident_id)

    data = await file.read()
    if len(data) == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Uploaded file is empty")
    if len(data) > MAX_ATTACHMENT_SIZE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File exceeds the {MAX_ATTACHMENT_SIZE // (1024 * 1024)} MB limit",
        )

    now = utcnow()
    attachment_doc = {
        "incident_id": oid,
        "kind": kind.value,
        "filename": file.filename or "upload",
        "content_type": file.content_type or "application/octet-stream",
        "size": len(data),
        "description": description,
        "data": data,
        "uploaded_at": now,
    }
    result = await db["attachments"].insert_one(attachment_doc)

    # Native datetime, not an ISO string: BSON stores it directly (unlike
    # the bare date/time fields _json_safe handles elsewhere).
    meta = AttachmentMeta(
        id=str(result.inserted_id),
        filename=attachment_doc["filename"],
        content_type=attachment_doc["content_type"],
        size=attachment_doc["size"],
        description=description,
        uploaded_at=now,
    ).model_dump()

    field = "incident_pictures" if kind == AttachmentKind.PICTURE else "supporting_document_files"
    timeline_entry = TimelineEntry(
        actor=current_user.full_name,
        action="attachment_added",
        note=f"Added {kind.value.replace('_', ' ')}: {attachment_doc['filename']}",
    ).model_dump()

    await db["incidents"].update_one(
        {"_id": oid},
        {
            "$push": {field: meta, "timeline": timeline_entry},
            "$set": {"updated_at": now},
        },
    )
    updated = await db["incidents"].find_one({"_id": oid})
    return incident_to_response(updated)


@router.get("/{incident_id}/attachments/{attachment_id}")
async def get_attachment(
    incident_id: str,
    attachment_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> Response:
    incident_oid = _object_id_or_404(incident_id)
    attachment_oid = _object_id_or_404(attachment_id, detail="Attachment not found")

    attachment = await db["attachments"].find_one({"_id": attachment_oid, "incident_id": incident_oid})
    if attachment is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    return Response(
        content=attachment["data"],
        media_type=attachment["content_type"],
        headers={"Content-Disposition": f'inline; filename="{attachment["filename"]}"'},
    )


@router.delete("/{incident_id}/attachments/{attachment_id}", response_model=IncidentResponse)
async def delete_attachment(
    incident_id: str,
    attachment_id: str,
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    incident_oid = _object_id_or_404(incident_id)
    attachment_oid = _object_id_or_404(attachment_id, detail="Attachment not found")
    await _get_incident_or_404(db, incident_id)

    result = await db["attachments"].delete_one({"_id": attachment_oid, "incident_id": incident_oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")

    now = utcnow()
    await db["incidents"].update_one(
        {"_id": incident_oid},
        {
            "$pull": {
                "incident_pictures": {"id": attachment_id},
                "supporting_document_files": {"id": attachment_id},
            },
            "$push": {
                "timeline": TimelineEntry(
                    actor=current_user.full_name, action="attachment_removed", note="Removed an attachment."
                ).model_dump()
            },
            "$set": {"updated_at": now},
        },
    )
    updated = await db["incidents"].find_one({"_id": incident_oid})
    return incident_to_response(updated)


# ---------------------------------------------------------------------------
# PDF export (Section-for-section facsimile of the paper form)
# ---------------------------------------------------------------------------


@router.get("/{incident_id}/pdf")
async def export_incident_pdf(
    incident_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> Response:
    oid = _object_id_or_404(incident_id)
    doc = await _get_incident_or_404(db, incident_id)
    incident = incident_to_response(doc)

    # Only image attachments get embedded in the PDF; fetch just those
    # bytes rather than every attachment on the incident.
    picture_ids = [
        ObjectId(p["id"])
        for p in incident.get("incident_pictures", [])
        if ObjectId.is_valid(p["id"]) and (p.get("content_type") or "").startswith("image/")
    ]
    picture_bytes: dict[str, bytes] = {}
    if picture_ids:
        cursor = db["attachments"].find({"_id": {"$in": picture_ids}, "incident_id": oid})
        async for attachment in cursor:
            picture_bytes[str(attachment["_id"])] = attachment["data"]

    pdf_bytes = build_incident_pdf(incident, picture_bytes)
    filename = f"incident-report-{incident_id}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
