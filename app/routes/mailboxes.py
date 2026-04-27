"""Mailbox management API (default + temporary mailboxes)."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import log_event
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Mailbox, User
from ..schemas import (
    CheckLocalPartOut,
    CreateTempMailboxIn,
    MailboxOut,
)
from ..utils import is_valid_localpart, random_localpart, slugify_localpart

router = APIRouter(prefix="/api/mailboxes", tags=["mailboxes"])

DEFAULT_TEMP_LIMIT = 10


def _get_temp_limit(db: Session) -> int:
    """Allow admins to override the temp mailbox limit via admin_settings."""
    from ..models import AdminSetting  # local import to avoid cycle

    row = (
        db.execute(select(AdminSetting).where(AdminSetting.key == "temp_mailbox_limit"))
        .scalars()
        .first()
    )
    if not row:
        return DEFAULT_TEMP_LIMIT
    val = row.value
    if isinstance(val, dict) and "value" in val:
        val = val["value"]
    try:
        return int(val)
    except (TypeError, ValueError):
        return DEFAULT_TEMP_LIMIT


@router.get("", response_model=list[MailboxOut])
def list_mailboxes(
    include_deleted: bool = Query(default=True),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    q = select(Mailbox).where(Mailbox.user_id == user.id)
    if not include_deleted:
        q = q.where(Mailbox.deleted_at.is_(None))
    q = q.order_by(
        Mailbox.is_default.desc(),
        Mailbox.deleted_at.is_(None).desc(),
        asc(Mailbox.created_at),
    )
    rows = db.execute(q).scalars().all()
    return rows


@router.get("/check", response_model=CheckLocalPartOut)
def check_local_part(
    local_part: str = Query(..., min_length=1, max_length=64),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cleaned = slugify_localpart(local_part)
    if not is_valid_localpart(cleaned):
        return CheckLocalPartOut(
            available=False,
            email=f"{cleaned or local_part}@{settings.APP_DOMAIN}",
            reason="Invalid local part. Use lowercase letters, numbers, dot, dash or underscore.",
        )
    full = f"{cleaned}@{settings.APP_DOMAIN}".lower()
    existing = (
        db.execute(
            select(Mailbox.id).where(
                func.lower(Mailbox.email_address) == full,
                Mailbox.deleted_at.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if existing:
        return CheckLocalPartOut(
            available=False,
            email=full,
            reason="This email address is already in use.",
        )
    return CheckLocalPartOut(available=True, email=full)


@router.post("/temp", response_model=MailboxOut, status_code=201)
def create_temp_mailbox(
    payload: CreateTempMailboxIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Active temp limit
    temp_limit = _get_temp_limit(db)
    active_temps = (
        db.execute(
            select(func.count(Mailbox.id)).where(
                Mailbox.user_id == user.id,
                Mailbox.type == "temp",
                Mailbox.deleted_at.is_(None),
            )
        )
        .scalar_one()
    )
    if active_temps >= temp_limit:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum {temp_limit} temporary emails allowed. Delete one before creating another.",
        )

    # Resolve localpart
    if payload.local_part:
        cleaned = slugify_localpart(payload.local_part)
        if not is_valid_localpart(cleaned):
            raise HTTPException(
                status_code=400,
                detail="Invalid local part. Use lowercase letters, digits, dot, dash, underscore.",
            )
    else:
        cleaned = random_localpart("temp")

    full = f"{cleaned}@{settings.APP_DOMAIN}".lower()

    # Check active uniqueness
    existing = (
        db.execute(
            select(Mailbox).where(
                func.lower(Mailbox.email_address) == full,
                Mailbox.deleted_at.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail="This email address is already in use.",
        )

    # If deleted version exists for this user, suggest restore
    deleted_mine = (
        db.execute(
            select(Mailbox).where(
                Mailbox.user_id == user.id,
                func.lower(Mailbox.email_address) == full,
                Mailbox.deleted_at.is_not(None),
            )
        )
        .scalars()
        .first()
    )
    if deleted_mine:
        raise HTTPException(
            status_code=409,
            detail="You previously had this address. Use the restore action instead.",
        )

    mailbox = Mailbox(
        user_id=user.id,
        email_address=full,
        local_part=cleaned,
        domain=settings.APP_DOMAIN,
        type="temp",
        is_default=False,
        is_active=True,
    )
    db.add(mailbox)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This email address is already in use.")

    log_event(
        db,
        action="mailbox.create_temp",
        user_id=user.id,
        metadata={"mailbox_id": str(mailbox.id), "email": mailbox.email_address},
    )
    db.commit()
    db.refresh(mailbox)
    return mailbox


@router.delete("/{mailbox_id}", response_model=MailboxOut)
def delete_mailbox(
    mailbox_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox or mailbox.user_id != user.id:
        raise HTTPException(status_code=404, detail="Mailbox not found.")
    if mailbox.is_default:
        raise HTTPException(status_code=400, detail="Default mailbox cannot be deleted.")
    if mailbox.deleted_at is not None:
        # Already deleted - no-op
        return mailbox

    mailbox.deleted_at = datetime.now(timezone.utc)
    mailbox.is_active = False
    log_event(
        db,
        action="mailbox.delete",
        user_id=user.id,
        metadata={"mailbox_id": str(mailbox.id), "email": mailbox.email_address},
    )
    db.commit()
    db.refresh(mailbox)
    return mailbox


@router.post("/{mailbox_id}/restore", response_model=MailboxOut)
def restore_mailbox(
    mailbox_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    mailbox = db.get(Mailbox, mailbox_id)
    if not mailbox or mailbox.user_id != user.id:
        raise HTTPException(status_code=404, detail="Mailbox not found.")
    if mailbox.deleted_at is None:
        return mailbox  # already active

    # Confirm no other active mailbox uses this address
    taken = (
        db.execute(
            select(Mailbox.id).where(
                Mailbox.id != mailbox.id,
                func.lower(Mailbox.email_address) == mailbox.email_address.lower(),
                Mailbox.deleted_at.is_(None),
            )
        )
        .scalars()
        .first()
    )
    if taken:
        raise HTTPException(
            status_code=409,
            detail="This email address has already been taken and cannot be restored.",
        )

    # If user respects temp_limit, also re-check
    if mailbox.type == "temp":
        temp_limit = _get_temp_limit(db)
        active_temps = (
            db.execute(
                select(func.count(Mailbox.id)).where(
                    Mailbox.user_id == user.id,
                    Mailbox.type == "temp",
                    Mailbox.deleted_at.is_(None),
                )
            )
            .scalar_one()
        )
        if active_temps >= temp_limit:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Maximum {temp_limit} temporary emails allowed. "
                    "Delete one before restoring."
                ),
            )

    mailbox.deleted_at = None
    mailbox.is_active = True
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This email address has already been taken and cannot be restored.",
        )

    log_event(
        db,
        action="mailbox.restore",
        user_id=user.id,
        metadata={"mailbox_id": str(mailbox.id), "email": mailbox.email_address},
    )
    db.commit()
    db.refresh(mailbox)
    return mailbox
