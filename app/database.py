"""Database engine + session factory and dependency."""

from __future__ import annotations

from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


def _build_engine_url(url: str) -> str:
    """Ensure SQLAlchemy uses the psycopg (3.x) driver explicitly.

    Neon URLs often start with ``postgresql://``; SQLAlchemy 2.x defaults
    to psycopg2 for that scheme, but we ship psycopg3, so we coerce it.
    """
    if not url:
        return url
    if url.startswith("postgresql+"):
        return url
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://"):]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://"):]
    return url


DATABASE_URL = _build_engine_url(settings.DATABASE_URL)

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL is not configured. Add it to your .env (Neon PostgreSQL URL)."
    )


engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    future=True,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
    future=True,
)


class Base(DeclarativeBase):
    """Base declarative class for all ORM models."""


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a SQLAlchemy session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
