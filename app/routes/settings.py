"""Per-user settings endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..database import get_db
from ..deps import get_current_user
from ..models import Mailbox, User
from ..schemas import UserSettingsIn

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _settings_dict(user: User) -> dict:
    base = {
        "display_name": (user.settings or {}).get("display_name") or user.name or "",
        "default_mailbox_id": (user.settings or {}).get("default_mailbox_id"),
        "emails_per_page": (user.settings or {}).get("emails_per_page", 20),
        "theme": (user.settings or {}).get("theme"),
    }
    extra = {k: v for k, v in (user.settings or {}).items() if k not in base}
    return {**base, "extra": extra}


@router.get("")
def get_settings(user: User = Depends(get_current_user)):
    return _settings_dict(user)


@router.post("")
def update_settings(
    payload: UserSettingsIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cur = dict(user.settings or {})
    if payload.display_name is not None:
        cur["display_name"] = payload.display_name.strip()
        if payload.display_name.strip():
            user.name = payload.display_name.strip()
    if payload.emails_per_page is not None:
        cur["emails_per_page"] = int(payload.emails_per_page)
    if payload.theme is not None:
        cur["theme"] = payload.theme

    if payload.default_mailbox_id is not None:
        mailbox = db.get(Mailbox, payload.default_mailbox_id)
        if not mailbox or mailbox.user_id != user.id or mailbox.deleted_at is not None:
            raise HTTPException(status_code=400, detail="Invalid default mailbox.")
        cur["default_mailbox_id"] = str(mailbox.id)
    if payload.extra:
        for k, v in payload.extra.items():
            cur[str(k)] = v

    user.settings = cur
    db.commit()
    db.refresh(user)
    return _settings_dict(user)
