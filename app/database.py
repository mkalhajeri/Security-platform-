"""MongoDB connection management for the platform.

Incidents are the first data type stored here, but this module is meant to
back all future security-platform data (assets, users, alerts, etc.) — every
collection lives in the same database and shares the same client.
"""

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import get_settings

settings = get_settings()


class Database:
    client: AsyncIOMotorClient | None = None
    db: AsyncIOMotorDatabase | None = None


database = Database()


async def connect_to_mongo() -> None:
    database.client = AsyncIOMotorClient(settings.mongodb_uri)
    database.db = database.client[settings.database_name]
    await create_indexes(database.db)


async def close_mongo_connection() -> None:
    if database.client is not None:
        database.client.close()


async def create_indexes(db: AsyncIOMotorDatabase) -> None:
    incidents = db["incidents"]
    await incidents.create_index("status")
    await incidents.create_index("site_location")
    await incidents.create_index("site_id")
    await incidents.create_index("incident_number", unique=True)
    await incidents.create_index("nature_of_report")
    await incidents.create_index("incident_categories")
    await incidents.create_index("incident_date")
    await incidents.create_index("created_at")
    await incidents.create_index(
        [
            ("incident_background", "text"),
            ("immediate_action_taken", "text"),
            ("root_cause", "text"),
            ("recommendations", "text"),
        ]
    )

    # Attachment bytes live here, keyed by their own _id; incidents only
    # embed the metadata (see AttachmentMeta / incident_to_response).
    await db["attachments"].create_index("incident_id")

    # Sites registry (see app/site_models.py): code is what seeds incident
    # numbers (app/numbering.py), so it must be unique.
    await db["sites"].create_index("code", unique=True)


def get_database() -> AsyncIOMotorDatabase:
    """FastAPI dependency returning the active database handle.

    Overridden in tests to point at a mock/in-memory database.
    """
    if database.db is None:
        raise RuntimeError("Database is not initialized. Did the app startup run?")
    return database.db
