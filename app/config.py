"""Application configuration, loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Security Platform API"
    app_version: str = "0.1.0"

    mongodb_uri: str = "mongodb://localhost:27017"
    database_name: str = "security_platform"

    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # Auth. jwt_secret_key MUST be overridden (env var or .env) in any
    # shared/production deployment — the default only works for a single
    # local dev process and is not a secret.
    jwt_secret_key: str = "dev-insecure-secret-change-me"
    jwt_algorithm: str = "HS256"
    # Short-lived by design now that a refresh token exists to renew it
    # silently — keeps the exposure window small if an access token ever
    # leaks. Previously 720 (12h) back when there was no refresh flow and
    # expiry just meant "please log in again."
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    # Bootstrap admin, created on first startup if no admin exists yet.
    # Change the password immediately after first login.
    admin_email: str = "admin@example.com"
    admin_password: str = "ChangeMe123!"
    admin_full_name: str = "Administrator"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
