"""Password hashing, JWT issuance/verification, and the FastAPI
dependencies routers use to require a logged-in user or a minimum role."""

import logging
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from bson import ObjectId
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.auth_models import ROLE_LEVEL, Role, UserPublic
from app.config import get_settings
from app.database import get_database

logger = logging.getLogger(__name__)
settings = get_settings()

# auto_error=False so a missing header surfaces as our own 401 message
# rather than FastAPI's generic "Not authenticated" with no detail.
_bearer_scheme = HTTPBearer(auto_error=False)


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        # Malformed hash in the DB — never crash the login attempt over it.
        return False


# ---------------------------------------------------------------------------
# JWT
# ---------------------------------------------------------------------------


def create_access_token(user_id: str, role: Role | str) -> str:
    # Callers pass the raw DB value (a plain string — MongoDB doesn't know
    # about the Role enum) as often as a Role member, so accept either.
    role_value = role.value if isinstance(role, Role) else role
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "role": role_value,
        "iat": now,
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def _decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired — please log in again.")
    except jwt.InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication token.")


# ---------------------------------------------------------------------------
# Bootstrap admin
# ---------------------------------------------------------------------------


async def ensure_bootstrap_admin(db: AsyncIOMotorDatabase) -> None:
    """Create the first admin account on a fresh database. Idempotent —
    does nothing once any admin exists."""

    await db["users"].create_index("email", unique=True)

    existing_admin = await db["users"].find_one({"role": Role.ADMIN.value})
    if existing_admin:
        return

    await db["users"].insert_one(
        {
            "email": settings.admin_email.lower(),
            "full_name": settings.admin_full_name,
            "role": Role.ADMIN.value,
            "hashed_password": hash_password(settings.admin_password),
            "is_active": True,
            "created_at": datetime.now(timezone.utc),
        }
    )
    if settings.admin_password == "ChangeMe123!":
        logger.warning(
            "Bootstrap admin account created with the DEFAULT password (%s). "
            "Log in and change it immediately — this is not safe to leave as-is "
            "outside of local development.",
            settings.admin_email,
        )
    else:
        logger.info("Bootstrap admin account created: %s", settings.admin_email)


# ---------------------------------------------------------------------------
# FastAPI dependencies
# ---------------------------------------------------------------------------


def user_to_public(doc: dict) -> UserPublic:
    """The one place a raw `users` doc becomes a `UserPublic` — shared by
    every router that hands a user back to the client, so a new field
    (like the saved-signature ones) only needs adding here."""
    return UserPublic(
        id=str(doc["_id"]),
        email=doc["email"],
        full_name=doc["full_name"],
        role=doc["role"],
        is_active=doc["is_active"],
        created_at=doc["created_at"],
        saved_signature_image=doc.get("saved_signature_image"),
        saved_signature_text=doc.get("saved_signature_text"),
    )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
    db: AsyncIOMotorDatabase = Depends(get_database),
) -> UserPublic:
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated.")

    payload = _decode_token(credentials.credentials)
    user_id = payload.get("sub")
    if not user_id or not ObjectId.is_valid(user_id):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid authentication token.")

    doc = await db["users"].find_one({"_id": ObjectId(user_id)})
    if doc is None or not doc.get("is_active", True):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account not found or disabled.")

    return user_to_public(doc)


def require_min_role(minimum: Role):
    """Dependency factory: the caller's role must be `minimum` or more
    senior (ADMIN always qualifies, being the top of the hierarchy)."""

    async def checker(current_user: UserPublic = Depends(get_current_user)) -> UserPublic:
        if ROLE_LEVEL[current_user.role] < ROLE_LEVEL[minimum]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"This action requires the {minimum.value.replace('_', ' ')} role or higher.",
            )
        return current_user

    return checker


require_admin = require_min_role(Role.ADMIN)
