"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env if present (no-op when running with env vars already set)
load_dotenv(BASE_DIR / ".env")


def _bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    val = os.getenv(name)
    if not val:
        return default
    try:
        return int(val)
    except (TypeError, ValueError):
        return default


class Settings:
    """Centralized application settings."""

    BASE_DIR: Path = BASE_DIR

    DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip()
    APP_SECRET_KEY: str = os.getenv(
        "APP_SECRET_KEY", "change-this-to-a-strong-random-secret"
    ).strip()
    WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "").strip()

    APP_DOMAIN: str = os.getenv("APP_DOMAIN", "softrise.app").strip().lower()
    BASE_URL: str = os.getenv("BASE_URL", "http://localhost:5000").rstrip("/")
    PORT: int = _int("PORT", 5000)

    APP_ENV: str = os.getenv("APP_ENV", "development").strip().lower()

    MAX_ATTACHMENT_SIZE_MB: int = _int("MAX_ATTACHMENT_SIZE_MB", 10)
    MAX_WEBHOOK_PAYLOAD_MB: int = _int("MAX_WEBHOOK_PAYLOAD_MB", 25)

    SESSION_COOKIE_NAME: str = "softrise_session"
    SESSION_TTL_HOURS: int = 24 * 14  # 14 days

    @property
    def is_production(self) -> bool:
        return self.APP_ENV in {"production", "prod"}

    @property
    def cookie_secure(self) -> bool:
        # NOTE: prefer the request-aware ``_is_https_request()`` in
        # ``app/routes/auth.py``.  This property only acts as a coarse
        # production fallback for code paths that don't have a Request.
        return self.is_production

    @property
    def attachment_dir(self) -> Path:
        path = self.BASE_DIR / "storage" / "attachments"
        path.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
