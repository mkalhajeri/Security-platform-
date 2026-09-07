"""CRUD endpoints for security incident reports."""

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import get_database
from app.models import (
    Category,
    IncidentCreate,
    IncidentListResponse,
    IncidentResponse,
    IncidentUpdate,
    Severity,
    Status,
    TimelineEntry,
    TimelineEntryCreate,
    incident_to_response,
    utcnow,
)

router = APIRouter(prefix="/incidents", tags=["incidents"])


def _object_id_or_404(incident_id: str) -> ObjectId:
    if not ObjectId.is_valid(incident_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return ObjectId(incident_id)


async def _get_incident_or_404(db: AsyncIOMotorDatabase, incident_id: str) -> dict:
    oid = _object_id_or_404(incident_id)
    doc = await db["incidents"].find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
    return doc


@router.post("", response_model=IncidentResponse, status_code=status.HTTP_201_CREATED)
async def create_incident(
    payload: IncidentCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    now = utcnow()
    doc = payload.model_dump()
    doc.update(
        {
            "status": Status.OPEN,
            "timeline": [
                TimelineEntry(
                    actor=payload.reporter_name,
                    action="created",
                    note="Incident reported.",
                ).model_dump()
            ],
            "created_at": now,
            "updated_at": now,
            "resolved_at": None,
        }
    )
    result = await db["incidents"].insert_one(doc)
    created = await db["incidents"].find_one({"_id": result.inserted_id})
    return incident_to_response(created)


@router.get("", response_model=IncidentListResponse)
async def list_incidents(
    db: AsyncIOMotorDatabase = Depends(get_database),
    status_filter: Status | None = Query(default=None, alias="status"),
    severity: Severity | None = Query(default=None),
    category: Category | None = Query(default=None),
    search: str | None = Query(default=None, description="Full-text search over title/description"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    query: dict = {}
    if status_filter is not None:
        query["status"] = status_filter.value
    if severity is not None:
        query["severity"] = severity.value
    if category is not None:
        query["category"] = category.value
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
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    oid = _object_id_or_404(incident_id)
    existing = await _get_incident_or_404(db, incident_id)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return incident_to_response(existing)

    timeline_additions = []
    now = utcnow()

    if "status" in updates and updates["status"] != existing.get("status"):
        new_status = updates["status"]
        timeline_additions.append(
            TimelineEntry(
                actor="system",
                action="status_change",
                note=f"Status changed from {existing.get('status')} to {new_status}",
            ).model_dump()
        )
        if new_status in (Status.RESOLVED.value, Status.CLOSED.value):
            updates["resolved_at"] = now
        else:
            updates["resolved_at"] = None

    if "severity" in updates and updates["severity"] != existing.get("severity"):
        timeline_additions.append(
            TimelineEntry(
                actor="system",
                action="severity_change",
                note=f"Severity changed from {existing.get('severity')} to {updates['severity']}",
            ).model_dump()
        )

    # Serialize enum values for storage
    for key in ("status", "severity", "category"):
        if key in updates and updates[key] is not None:
            updates[key] = updates[key].value if hasattr(updates[key], "value") else updates[key]

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
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    oid = _object_id_or_404(incident_id)
    await _get_incident_or_404(db, incident_id)

    new_entry = TimelineEntry(**entry.model_dump())
    await db["incidents"].update_one(
        {"_id": oid},
        {
            "$push": {"timeline": new_entry.model_dump()},
            "$set": {"updated_at": utcnow()},
        },
    )
    updated = await db["incidents"].find_one({"_id": oid})
    return incident_to_response(updated)


@router.delete("/{incident_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_incident(
    incident_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> None:
    oid = _object_id_or_404(incident_id)
    result = await db["incidents"].delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Incident not found")
