"""Admin panel APIs (role='admin' only)."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import asc, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import log_event
from ..config import settings
from ..database import get_db
from ..deps import get_current_admin
from ..models import (
    AdminSetting,
    AuditLog,
    EmailAttachment,
    EmailMessage,
    Mailbox,
    User,
)
from ..schemas import (
    AdminMailboxCreateIn,
    AdminSettingsIn,
    AdminUserListItem,
    AdminUserUpdateIn,
    AuditLogOut,
    MailboxOut,
)
from ..utils import is_valid_localpart, slugify_localpart

router = APIRouter(prefix="/api/admin", tags=["admin"])


DEFAULT_ADMIN_SETTINGS: dict[str, Any] = {
    "temp_mailbox_limit": 10,
    "allow_custom_temp_email": True,
    "email_domain": "softrise.app",
    "max_attachment_size_mb": 10,
    "webhook_enabled": True,
}


# ---------- Stats ----------


@router.get("/stats")
def stats(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    total_users = db.execute(select(func.count(User.id))).scalar_one()
    active_users = db.execute(
        select(func.count(User.id)).where(User.is_active.is_(True))
    ).scalar_one()
    total_mailboxes = db.execute(select(func.count(Mailbox.id))).scalar_one()
    active_temp = db.execute(
        select(func.count(Mailbox.id)).where(
            Mailbox.type == "temp", Mailbox.deleted_at.is_(None)
        )
    ).scalar_one()
    active_default = db.execute(
        select(func.count(Mailbox.id)).where(
            Mailbox.type == "default", Mailbox.deleted_at.is_(None)
        )
    ).scalar_one()
    total_messages = db.execute(select(func.count(EmailMessage.id))).scalar_one()
    messages_today = db.execute(
        select(func.count(EmailMessage.id)).where(EmailMessage.created_at >= today_start)
    ).scalar_one()
    attachments_count = db.execute(select(func.count(EmailAttachment.id))).scalar_one()
    total_attachment_bytes = (
        db.execute(select(func.coalesce(func.sum(EmailAttachment.size), 0))).scalar_one() or 0
    )

    return {
        "total_users": int(total_users),
        "active_users": int(active_users),
        "total_mailboxes": int(total_mailboxes),
        "active_temp_mailboxes": int(active_temp),
        "active_default_mailboxes": int(active_default),
        "total_messages": int(total_messages),
        "messages_today": int(messages_today),
        "attachments_count": int(attachments_count),
        "attachments_total_bytes": int(total_attachment_bytes),
    }


# ---------- Users ----------


@router.get("/users")
def list_users(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(default=None),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    base = select(User)
    if search:
        like = f"%{search.strip()}%"
        base = base.where(
            or_(User.username.ilike(like), User.email.ilike(like), User.name.ilike(like))
        )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    pages = max(1, math.ceil(total / limit))

    users = (
        db.execute(
            base.order_by(desc(User.created_at)).offset((page - 1) * limit).limit(limit)
        )
        .scalars()
        .all()
    )
    if users:
        ids = [u.id for u in users]
        mb_counts = dict(
            db.execute(
                select(Mailbox.user_id, func.count(Mailbox.id))
                .where(Mailbox.user_id.in_(ids))
                .group_by(Mailbox.user_id)
            ).all()
        )
        msg_counts = dict(
            db.execute(
                select(EmailMessage.user_id, func.count(EmailMessage.id))
                .where(EmailMessage.user_id.in_(ids))
                .group_by(EmailMessage.user_id)
            ).all()
        )
    else:
        mb_counts = {}
        msg_counts = {}

    items = [
        AdminUserListItem(
            id=u.id,
            name=u.name,
            username=u.username,
            email=u.email,
            role=u.role,
            is_active=u.is_active,
            created_at=u.created_at,
            mailbox_count=int(mb_counts.get(u.id, 0)),
            message_count=int(msg_counts.get(u.id, 0)),
        )
        for u in users
    ]
    return {"items": items, "page": page, "limit": limit, "total": total, "pages": pages}


@router.patch("/users/{user_id}")
def update_user(
    user_id: UUID,
    payload: AdminUserUpdateIn,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    # Prevent dangerous self-lockout
    if target.id == admin.id:
        if payload.role and payload.role != "admin":
            other_admin = db.execute(
                select(func.count(User.id)).where(
                    User.role == "admin", User.is_active.is_(True), User.id != admin.id
                )
            ).scalar_one()
            if not other_admin:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot demote the only active admin.",
                )
        if payload.is_active is False:
            other_admin = db.execute(
                select(func.count(User.id)).where(
                    User.role == "admin", User.is_active.is_(True), User.id != admin.id
                )
            ).scalar_one()
            if not other_admin:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot deactivate the only active admin.",
                )

    if payload.name is not None:
        target.name = payload.name.strip()
    if payload.role is not None:
        target.role = payload.role
    if payload.is_active is not None:
        target.is_active = bool(payload.is_active)

    log_event(
        db,
        action="admin.user.update",
        user_id=admin.id,
        metadata={
            "target_user": str(target.id),
            "changes": payload.model_dump(exclude_unset=True),
        },
    )
    db.commit()
    db.refresh(target)
    return {
        "id": str(target.id),
        "username": target.username,
        "role": target.role,
        "is_active": target.is_active,
    }


# ---------- Mailboxes ----------


@router.get("/mailboxes")
def list_mailboxes_admin(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(default=None),
    user_id: Optional[UUID] = Query(default=None),
    status_filter: Optional[str] = Query(default=None, alias="status"),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    base = select(Mailbox)
    if search:
        like = f"%{search.strip()}%"
        base = base.where(Mailbox.email_address.ilike(like))
    if user_id:
        base = base.where(Mailbox.user_id == user_id)
    if status_filter == "active":
        base = base.where(Mailbox.deleted_at.is_(None))
    elif status_filter == "deleted":
        base = base.where(Mailbox.deleted_at.is_not(None))
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    pages = max(1, math.ceil(total / limit))
    rows = (
        db.execute(
            base.order_by(desc(Mailbox.created_at))
            .offset((page - 1) * limit)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    items = [
        {
            **MailboxOut.model_validate(row).model_dump(mode="json"),
            "user_id": str(row.user_id),
        }
        for row in rows
    ]
    return {"items": items, "page": page, "limit": limit, "total": total, "pages": pages}


@router.post("/users/{user_id}/mailboxes", status_code=201)
def admin_create_mailbox(
    user_id: UUID,
    payload: AdminMailboxCreateIn,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found.")

    cleaned = slugify_localpart(payload.local_part)
    if not is_valid_localpart(cleaned):
        raise HTTPException(status_code=400, detail="Invalid local part.")
    full = f"{cleaned}@{settings.APP_DOMAIN}".lower()
    existing = db.execute(
        select(Mailbox.id).where(
            func.lower(Mailbox.email_address) == full,
            Mailbox.deleted_at.is_(None),
        )
    ).scalars().first()
    if existing:
        raise HTTPException(status_code=409, detail="This email address is already in use.")

    if payload.type == "default":
        # Demote any previous default for this user
        db.execute(
            Mailbox.__table__.update()
            .where(Mailbox.user_id == target.id, Mailbox.is_default.is_(True))
            .values(is_default=False)
        )

    mb = Mailbox(
        user_id=target.id,
        email_address=full,
        local_part=cleaned,
        domain=settings.APP_DOMAIN,
        type=payload.type,
        is_default=(payload.type == "default"),
        is_active=True,
    )
    db.add(mb)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Mailbox already exists.")

    log_event(
        db,
        action="admin.mailbox.create",
        user_id=admin.id,
        metadata={
            "target_user": str(target.id),
            "mailbox_id": str(mb.id),
            "email": mb.email_address,
            "type": mb.type,
        },
    )
    db.commit()
    db.refresh(mb)
    return MailboxOut.model_validate(mb)


@router.delete("/mailboxes/{mailbox_id}")
def admin_delete_mailbox(
    mailbox_id: UUID,
    confirm_default: bool = Query(default=False),
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    mb = db.get(Mailbox, mailbox_id)
    if not mb:
        raise HTTPException(status_code=404, detail="Mailbox not found.")
    if mb.is_default and not confirm_default:
        raise HTTPException(
            status_code=400,
            detail="Refusing to delete a default mailbox without ?confirm_default=true",
        )
    if mb.deleted_at is not None:
        return MailboxOut.model_validate(mb)

    mb.deleted_at = datetime.now(timezone.utc)
    mb.is_active = False
    log_event(
        db,
        action="admin.mailbox.delete",
        user_id=admin.id,
        metadata={"mailbox_id": str(mb.id), "email": mb.email_address},
    )
    db.commit()
    db.refresh(mb)
    return MailboxOut.model_validate(mb)


@router.post("/mailboxes/{mailbox_id}/restore")
def admin_restore_mailbox(
    mailbox_id: UUID,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    mb = db.get(Mailbox, mailbox_id)
    if not mb:
        raise HTTPException(status_code=404, detail="Mailbox not found.")
    if mb.deleted_at is None:
        return MailboxOut.model_validate(mb)
    taken = db.execute(
        select(Mailbox.id).where(
            Mailbox.id != mb.id,
            func.lower(Mailbox.email_address) == mb.email_address.lower(),
            Mailbox.deleted_at.is_(None),
        )
    ).scalars().first()
    if taken:
        raise HTTPException(
            status_code=409,
            detail="This email address has already been taken and cannot be restored.",
        )
    mb.deleted_at = None
    mb.is_active = True
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Conflict restoring mailbox.")
    log_event(
        db,
        action="admin.mailbox.restore",
        user_id=admin.id,
        metadata={"mailbox_id": str(mb.id), "email": mb.email_address},
    )
    db.commit()
    db.refresh(mb)
    return MailboxOut.model_validate(mb)


# ---------- Messages ----------


@router.get("/messages")
def admin_list_messages(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(default=None),
    user_id: Optional[UUID] = Query(default=None),
    mailbox_id: Optional[UUID] = Query(default=None),
    folder: Optional[str] = Query(default=None),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    base = select(EmailMessage)
    if user_id:
        base = base.where(EmailMessage.user_id == user_id)
    if mailbox_id:
        base = base.where(EmailMessage.mailbox_id == mailbox_id)
    if folder:
        if folder == "trash":
            base = base.where(
                or_(EmailMessage.folder == "trash", EmailMessage.is_deleted.is_(True))
            )
        else:
            base = base.where(EmailMessage.folder == folder)
    if search:
        like = f"%{search.strip()}%"
        base = base.where(
            or_(
                EmailMessage.subject.ilike(like),
                EmailMessage.from_email.ilike(like),
                EmailMessage.to_email.ilike(like),
            )
        )
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    pages = max(1, math.ceil(total / limit))
    rows = (
        db.execute(
            base.order_by(desc(EmailMessage.received_at))
            .offset((page - 1) * limit)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": str(m.id),
            "user_id": str(m.user_id),
            "mailbox_id": str(m.mailbox_id),
            "from_email": m.from_email,
            "to_email": m.to_email,
            "subject": m.subject,
            "folder": m.folder,
            "is_read": m.is_read,
            "is_starred": m.is_starred,
            "is_deleted": m.is_deleted,
            "received_at": m.received_at.isoformat(),
            "size": m.size,
        }
        for m in rows
    ]
    return {"items": items, "page": page, "limit": limit, "total": total, "pages": pages}


# ---------- Settings ----------


def _all_settings(db: Session) -> dict[str, Any]:
    rows = db.execute(select(AdminSetting)).scalars().all()
    out = dict(DEFAULT_ADMIN_SETTINGS)
    for r in rows:
        v = r.value
        if isinstance(v, dict) and set(v.keys()) == {"value"}:
            v = v["value"]
        out[r.key] = v
    return out


@router.get("/settings")
def admin_get_settings(
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    return _all_settings(db)


@router.post("/settings")
def admin_post_settings(
    payload: AdminSettingsIn,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    for key, val in (payload.settings or {}).items():
        if not isinstance(key, str) or not key:
            continue
        row = db.execute(
            select(AdminSetting).where(AdminSetting.key == key)
        ).scalars().first()
        if row:
            row.value = {"value": val}
        else:
            row = AdminSetting(key=key, value={"value": val})
            db.add(row)
    log_event(
        db,
        action="admin.settings.update",
        user_id=admin.id,
        metadata={"keys": list((payload.settings or {}).keys())},
    )
    db.commit()
    return _all_settings(db)


# ---------- Audit Logs ----------


@router.get("/audit-logs")
def admin_audit_logs(
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=500),
    action: Optional[str] = Query(default=None),
    user_id: Optional[UUID] = Query(default=None),
    _admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    base = select(AuditLog)
    if action:
        base = base.where(AuditLog.action == action)
    if user_id:
        base = base.where(AuditLog.user_id == user_id)
    total = db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    pages = max(1, math.ceil(total / limit))
    rows = (
        db.execute(
            base.order_by(desc(AuditLog.created_at))
            .offset((page - 1) * limit)
            .limit(limit)
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": str(r.id),
            "user_id": str(r.user_id) if r.user_id else None,
            "action": r.action,
            "metadata": r.extra_metadata or {},
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]
    return {"items": items, "page": page, "limit": limit, "total": total, "pages": pages}
