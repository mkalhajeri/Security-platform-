"""Account management. There is no public self-registration — accounts are
provisioned manually by an admin or a management-level user for named
security-department staff.

Management may provision and manage accounts too (not just admin), but
only ones below its own level in the paper form's own hierarchy —
Security Officer and Security Supervisor. It can never create, edit, or
delete a Management or Admin account (including its own role/account),
which stops a management-level login from minting a peer or promoting
itself/anyone else to Admin. Admin is unrestricted, as ever.
"""

from datetime import datetime, timezone

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.auth import hash_password, require_min_role
from app.auth_models import ROLE_LEVEL, Role, UserCreate, UserPublic, UserUpdate
from app.database import get_database

router = APIRouter(prefix="/users", tags=["users"], dependencies=[Depends(require_min_role(Role.MANAGEMENT))])


def _assert_can_manage_role(current_user: UserPublic, target_role: Role, action: str) -> None:
    """Enforce the hierarchy note above. `target_role` is whichever role is
    actually at stake for this check — the account being acted on, or (for
    a role change) the role it would become. Admin always passes."""
    if current_user.role == Role.ADMIN:
        return
    if ROLE_LEVEL[target_role] >= ROLE_LEVEL[Role.MANAGEMENT]:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"Management accounts can only {action} Security Officer or Security Supervisor accounts.",
        )


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
    current_user: UserPublic = Depends(require_min_role(Role.MANAGEMENT)),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> UserPublic:
    _assert_can_manage_role(current_user, payload.role, "create")
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
    current_user: UserPublic = Depends(require_min_role(Role.MANAGEMENT)),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> UserPublic:
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    oid = ObjectId(user_id)

    target = await db["users"].find_one({"_id": oid})
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    # Checked against the account's *current* role — a management-level
    # caller can't reach a Management or Admin account at all here, even
    # just to change its name.
    _assert_can_manage_role(current_user, Role(target["role"]), "edit")

    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        return _to_public(target)

    if updates.get("role") is not None:
        # ...and checked again against the role it would become, so
        # management can't promote a Security Officer straight to Admin.
        _assert_can_manage_role(current_user, updates["role"], "assign")

    # Locking yourself out (losing admin, or deactivating your own account)
    # is a real footgun with no recovery path but the database console —
    # block it outright rather than trusting the UI to prevent it. (Only an
    # admin ever reaches this on their own account — the check above
    # already stops a management-level caller from targeting themselves,
    # since they're never "below" their own level.)
    if user_id == current_user.id:
        if updates.get("role") is not None and updates["role"].value != current_user.role.value:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot change your own role.")
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
    current_user: UserPublic = Depends(require_min_role(Role.MANAGEMENT)),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> None:
    if user_id == current_user.id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account.")
    if not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    oid = ObjectId(user_id)

    target = await db["users"].find_one({"_id": oid})
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
    _assert_can_manage_role(current_user, Role(target["role"]), "delete")

    result = await db["users"].delete_one({"_id": oid})
    if result.deleted_count == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")
