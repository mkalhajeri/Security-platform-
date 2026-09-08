"""Login, refresh, session revocation, and the current user's own account."""

from bson import ObjectId
from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth import (
    bump_token_version,
    create_access_token,
    create_refresh_token,
    decode_token,
    get_current_user,
    hash_password,
    load_active_user_for_token,
    user_to_public,
    verify_password,
)
from app.auth_models import (
    AccessTokenResponse,
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    SignatureUpdate,
    TokenResponse,
    UserPublic,
)
from app.database import get_database

router = APIRouter(prefix="/auth", tags=["auth"])


def _issue_tokens(doc: dict) -> dict:
    user_id = str(doc["_id"])
    token_version = doc.get("token_version", 0)
    return {
        "access_token": create_access_token(user_id, doc["role"], token_version),
        "refresh_token": create_refresh_token(user_id, doc["role"], token_version),
        "token_type": "bearer",
        "user": user_to_public(doc),
    }


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

    return _issue_tokens(doc)


@router.post("/refresh", response_model=AccessTokenResponse)
async def refresh_access_token(
    payload: RefreshRequest,
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    """Exchange a still-valid refresh token for a new access token, without
    re-entering a password. Rejected the same way an access token would be
    if the account's been deactivated or every session revoked since this
    refresh token was issued (see bump_token_version)."""
    token_payload = decode_token(payload.refresh_token, expected_type="refresh")
    doc = await load_active_user_for_token(db, token_payload)
    access_token = create_access_token(str(doc["_id"]), doc["role"], doc.get("token_version", 0))
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/me", response_model=UserPublic)
async def read_me(current_user: UserPublic = Depends(get_current_user)) -> UserPublic:
    return current_user


@router.post("/change-password", response_model=TokenResponse)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> dict:
    doc = await db["users"].find_one({"_id": ObjectId(current_user.id)})
    if doc is None or not verify_password(payload.current_password, doc["hashed_password"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Current password is incorrect.")

    await db["users"].update_one(
        {"_id": ObjectId(current_user.id)},
        {"$set": {"hashed_password": hash_password(payload.new_password)}},
    )
    # A changed password should log out every *other* session immediately
    # (someone who had the old password may have an active token) — but
    # the caller just proved they know the new one, so hand back a fresh
    # pair rather than logging them out too along with everyone else.
    await bump_token_version(db, current_user.id)
    doc = await db["users"].find_one({"_id": ObjectId(current_user.id)})
    return _issue_tokens(doc)


@router.post("/logout-everywhere", status_code=status.HTTP_204_NO_CONTENT)
async def logout_everywhere(
    current_user: UserPublic = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> None:
    """Invalidate every access/refresh token issued to this account so
    far — including the one used to call this endpoint. For "I think I
    left myself logged in somewhere" / "my token may have leaked", without
    needing to know every device involved."""
    await bump_token_version(db, current_user.id)


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
