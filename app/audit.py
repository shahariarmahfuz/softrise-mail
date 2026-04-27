"""Tiny helper for writing audit log rows without leaking errors."""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from .models import AuditLog

logger = logging.getLogger(__name__)


def log_event(
    db: Session,
    *,
    action: str,
    user_id: Optional[UUID] = None,
    metadata: Optional[dict[str, Any]] = None,
    commit: bool = False,
) -> None:
    """Insert an audit log row.

    ``commit=False`` is the default because most callers manage their own
    transaction; flip it on for fire-and-forget calls outside a unit-of-work.
    """
    try:
        entry = AuditLog(
            user_id=user_id,
            action=action,
            extra_metadata=metadata or {},
        )
        db.add(entry)
        db.flush()
        if commit:
            db.commit()
    except Exception as exc:  # pragma: no cover - never fail caller
        logger.warning("audit_log failed for action=%s: %s", action, exc)
        if commit:
            db.rollback()
