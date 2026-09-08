"""Managed registry of sites (facilities/locations) an incident can be
filed against.

Any logged-in user can list sites — the New Report form needs the active
list to populate its dropdown — but creating, renaming, or
deactivating/reactivating a site is admin-only, mirroring routers/users.py.

A site's `code` seeds every incident number filed against it (see
app/numbering.py), so it's immutable after creation and a site with
existing incidents can't be deleted outright — deactivate it instead,
which hides it from the New Report dropdown while keeping past incidents'
site reference intact.
"""

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.auth import get_current_user, require_admin
from app.database import get_database
from app.site_models import SiteCreate, SitePublic, SiteUpdate, utcnow

router = APIRouter(prefix="/sites", tags=["sites"], dependencies=[Depends(get_current_user)])


def _to_public(doc: dict) -> SitePublic:
    return SitePublic(
        id=str(doc["_id"]),
        code=doc["code"],
        name=doc["name"],
        is_active=doc.get("is_active", True),
        created_at=doc["created_at"],
    )


@router.post(
    "",
    response_model=SitePublic,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_admin)],
)
async def create_site(
    payload: SiteCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> SitePublic:
    doc = {
        "code": payload.code,
        "name": payload.name,
        "is_active": True,
        "created_at": utcnow(),
    }
    try:
        result = await db["sites"].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "A site with this code already exists.")
    doc["_id"] = result.inserted_id
    return _to_public(doc)


@router.get("", response_model=list[SitePublic])
async def list_sites(
    include_inactive: bool = False,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> list[SitePublic]:
    query: dict = {} if include_inactive else {"is_active": True}
    return [_to_public(doc) async for doc in db["sites"].find(query).sort("name", 1)]


@router.patch("/{site_id}", response_model=SitePublic, dependencies=[Depends(require_admin)])
async def update_site(
    site_id: str,
    payload: SiteUpdate,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> SitePublic:
    if not ObjectId.is_valid(site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")
    oid = ObjectId(site_id)

    updates = payload.model_dump(exclude_unset=True)
    if updates:
        result = await db["sites"].update_one({"_id": oid}, {"$set": updates})
        if result.matched_count == 0:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")

    doc = await db["sites"].find_one({"_id": oid})
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")
    return _to_public(doc)


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_admin)])
async def delete_site(
    site_id: str,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> None:
    if not ObjectId.is_valid(site_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")
    oid = ObjectId(site_id)

    in_use = await db["incidents"].count_documents({"site_id": site_id})
    if in_use:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{in_use} incident(s) reference this site — deactivate it instead of deleting it.",
        )

    result = await db["sites"].delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Site not found.")
