"""Sync-watch backend settings."""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "survivor.lol sync-watch"
    database_url: str = "sqlite+aiosqlite:///./dev.db"
    # Postgres example: postgresql+asyncpg://survivor:survivor@db:5432/survivor
    jwt_secret: str = "dev-secret-change-me"
    jwt_algorithm: str = "HS256"
    room_token_ttl_hours: int = 12
    media_dir: str = "./media"
    max_upload_mb: int = 2048
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    # Target sync tolerance note: true sub-ms is not achievable over WAN;
    # protocol compensates with server timestamps (see services/sync.py).

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
