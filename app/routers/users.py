"""Admin-only account management. There is no public self-registration —
accounts are provisioned by an admin for named security-department staff."""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.auth import hash_password, require_admin
from app.auth_models import UserCreate, UserPublic, UserUpdate
from app.database import get_database

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_admin)])


def _to_public(doc: dict) -> UserPublic:
    return UserPublic(
        id=str(doc["_id"]),
        email=doc["email"],
        full_name=doc["full_name"],
        role=doc["role"],
        is_active=doc["is_active"],
        created_at=doc["created_at"],
    )


@router.post("", response_model=UserPublic, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> UserPublic:
    doc = {
        "email": payload.email.lower(),
        "full_name": payload.full_name,
        "role": payload.role.value,
        "hashed_password": hash_password(payload.password),
        "is_active": True,
        "created_at": datetime.now(timezone.utc),
    }
    try:
        result = await db["users"].insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(status.HTTP_409_CONFLICT, "An account with this email already exists.")
    doc["_id"] = result.inserted_id
    return _to_public(doc)


@router.get("", response_model=list[UserPublic])
async def list_users(db: AsyncIOMotorDatabase = Depends(get_database)) -> list[UserPublic]:
    return [_to_public(doc) async for doc in db["users"].find().sort("created_at", 1)]


@router.patch("/{user_id}", response_model=UserPublic)
async def update_user(
    user_id: str,
    payload: UserUpdate,
    current_user: UserPublic = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> UserPublic:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    oid = ObjectId(user_id)

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        doc = await db["users"].find_one({"_id": oid})
        if doc is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
        return _to_public(doc)

    # An admin locking themselves out (losing admin, or deactivating their
    # own account) is a real footgun with no recovery path but the database
    # console — block it outright rather than trusting the UI to prevent it.
    if user_id == current_user.id:
        if updates.get("role") is not None and updates["role"].value != "admin":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot remove your own admin role.")
        if updates.get("is_active") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot deactivate your own account.")

    if "role" in updates and updates["role"] is not None:
        updates["role"] = updates["role"].value
    if "password" in updates and updates["password"]:
        updates["hashed_password"] = hash_password(updates.pop("password"))
    elif "password" in updates:
        updates.pop("password")

    result = await db["users"].update_one({"_id": oid}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    doc = await db["users"].find_one({"_id": oid})
    return _to_public(doc)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_id: str,
    current_user: UserPublic = Depends(require_admin),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> None:
    if user_id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account.")
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    result = await db["users"].delete_one({"_id": ObjectId(user_id)})
    if result.deleted_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
