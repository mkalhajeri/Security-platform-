"""Security Platform API entrypoint.

Starts with the incident reporting system backed by MongoDB. Future
platform modules (assets, users, alerts, integrations, ...) are expected
to register their own routers here and share the same database.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.auth import ensure_bootstrap_admin
from app.config import get_settings
from app.database import close_mongo_connection, connect_to_mongo, database
from app.routers import analytics, auth, health, incidents, sites, users

STATIC_DIR = Path(__file__).parent / "static"

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_to_mongo()
    await ensure_bootstrap_admin(database.db)
    yield
    await close_mongo_connection()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(sites.router)
app.include_router(incidents.router)
app.include_router(analytics.router)


@app.get("/api/info")
async def api_info() -> dict:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
    }


# Basic web UI (static HTML/CSS/JS, no build step) — mounted last so it
# only catches requests not handled by the API routes above. html=True
# serves static/index.html for "/".
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
