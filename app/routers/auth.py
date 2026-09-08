"""Login and the current user's own account."""

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth import create_access_token, get_current_user, hash_password, user_to_public, verify_password
from app.auth_models import ChangePasswordRequest, LoginRequest, SignatureUpdate, TokenResponse, UserPublic
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
    return {"access_token": token, "token_type": "bearer", "user": user_to_public(doc)}


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


@router.put("/me/signature", response_model=UserPublic)
async def update_my_signature(
    payload: SignatureUpdate,
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> UserPublic:
    """Save (or clear, by sending both fields null) the caller's own
    reusable signature — self-service only, mirroring change-password
    above. Once saved, the web UI's signature pad offers it as a starting
    point on future sign-offs instead of everyone drawing fresh each time."""
    await db["users"].update_one(
        {"_id": ObjectId(current_user.id)},
        {"$set": {"saved_signature_image": payload.signature_image, "saved_signature_text": payload.signature}},
    )
    doc = await db["users"].find_one({"_id": ObjectId(current_user.id)})
    return user_to_public(doc)
