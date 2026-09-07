"""Login and the current user's own account."""

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth import create_access_token, get_current_user, hash_password, verify_password
from app.auth_models import ChangePasswordRequest, LoginRequest, TokenResponse, UserPublic
from app.database import get_database

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    doc = await db["users"].find_one({"email": payload.email.lower()})
    # Same error for "no such account" and "wrong password" — don't help
    # an attacker enumerate which emails have accounts.
    invalid = HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password.")
    if doc is None or not verify_password(payload.password, doc["hashed_password"]):
        raise invalid
    if not doc.get("is_active", True):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This account has been disabled.")

    token = create_access_token(str(doc["_id"]), doc["role"])
    user = UserPublic(
        id=str(doc["_id"]),
        email=doc["email"],
        full_name=doc["full_name"],
        role=doc["role"],
        is_active=doc["is_active"],
        created_at=doc["created_at"],
    )
    return {"access_token": token, "token_type": "bearer", "user": user}


@router.get("/me", response_model=UserPublic)
async def read_me(current_user: UserPublic = Depends(get_current_user)) -> UserPublic:
    return current_user


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> None:
    doc = await db["users"].find_one({"_id": ObjectId(current_user.id)})
    if doc is None or not verify_password(payload.current_password, doc["hashed_password"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect.")

    await db["users"].update_one(
        {"_id": ObjectId(current_user.id)},
        {"$set": {"hashed_password": hash_password(payload.new_password)}},
    )
