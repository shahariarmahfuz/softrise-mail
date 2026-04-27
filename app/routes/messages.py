"""Email message APIs (list / get / read / star / archive / trash / delete)."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, asc, desc, func, or_, select, update
from sqlalchemy.orm import Session

from ..audit import log_event
from ..database import get_db
from ..deps import get_current_user
from ..models import EmailAttachment, EmailMessage, Mailbox, User
from ..schemas import (
    AttachmentOut,
    BulkActionIn,
    MessageDetail,
    MessageList,
    MessageListItem,
    ReadAllIn,
    ToggleReadIn,
    ToggleStarIn,
)
from ..utils import make_snippet, sanitize_email_html

router = APIRouter(prefix="/api/messages", tags=["messages"])

VALID_FOLDERS = {"inbox", "archive", "trash", "starred"}


def _user_message(db: Session, user: User, message_id: UUID) -> EmailMessage:
    msg = db.get(EmailMessage, message_id)
    if not msg or msg.user_id != user.id:
        raise HTTPException(status_code=404, detail="Message not found.")
    return msg


def _apply_folder_filter(
    stmt,
    *,
    folder: Optional[str],
    starred: Optional[bool],
    read: Optional[bool],
):
    if folder == "trash":
        stmt = stmt.where(or_(EmailMessage.folder == "trash", EmailMessage.is_deleted.is_(True)))
    elif folder == "archive":
        stmt = stmt.where(EmailMessage.folder == "archive", EmailMessage.is_deleted.is_(False))
    elif folder == "starred":
        stmt = stmt.where(EmailMessage.is_starred.is_(True), EmailMessage.is_deleted.is_(False))
    elif folder == "inbox":
        stmt = stmt.where(EmailMessage.folder == "inbox", EmailMessage.is_deleted.is_(False))
    elif folder is None:
        if starred is not True:
            stmt = stmt.where(EmailMessage.folder == "inbox", EmailMessage.is_deleted.is_(False))
        else:
            stmt = stmt.where(EmailMessage.is_deleted.is_(False))
    elif folder == "all":
        stmt = stmt.where(EmailMessage.is_deleted.is_(False))
    else:
        stmt = stmt.where(EmailMessage.folder == folder, EmailMessage.is_deleted.is_(False))

    if starred is True:
        stmt = stmt.where(EmailMessage.is_starred.is_(True))
    elif starred is False:
        stmt = stmt.where(EmailMessage.is_starred.is_(False))

    if read is True:
        stmt = stmt.where(EmailMessage.is_read.is_(True))
    elif read is False:
        stmt = stmt.where(EmailMessage.is_read.is_(False))

    return stmt


@router.get("", response_model=MessageList)
def list_messages(
    folder: Optional[str] = Query(default=None),
    mailbox_id: Optional[UUID] = Query(default=None),
    search: Optional[str] = Query(default=None, max_length=200),
    starred: Optional[bool] = Query(default=None),
    read: Optional[bool] = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    base = select(EmailMessage).where(EmailMessage.user_id == user.id)
    if mailbox_id:
        base = base.where(EmailMessage.mailbox_id == mailbox_id)
    base = _apply_folder_filter(base, folder=folder, starred=starred, read=read)
    if search:
        like = f"%{search.strip()}%"
        base = base.where(
            or_(
                EmailMessage.subject.ilike(like),
                EmailMessage.from_email.ilike(like),
                EmailMessage.from_name.ilike(like),
                EmailMessage.to_email.ilike(like),
                EmailMessage.body_text.ilike(like),
            )
        )

    total = (
        db.execute(select(func.count()).select_from(base.subquery())).scalar_one()
    )
    pages = max(1, math.ceil(total / limit))

    stmt = (
        base.order_by(desc(EmailMessage.received_at), desc(EmailMessage.created_at))
        .offset((page - 1) * limit)
        .limit(limit)
    )
    messages = db.execute(stmt).scalars().all()

    # Attachments count map
    if messages:
        ids = [m.id for m in messages]
        att_rows = db.execute(
            select(EmailAttachment.email_message_id, func.count(EmailAttachment.id))
            .where(EmailAttachment.email_message_id.in_(ids))
            .group_by(EmailAttachment.email_message_id)
        ).all()
        att_map = {mid: cnt for mid, cnt in att_rows}
    else:
        att_map = {}

    items = [
        MessageListItem(
            id=m.id,
            mailbox_id=m.mailbox_id,
            from_email=m.from_email,
            from_name=m.from_name,
            to_email=m.to_email,
            subject=m.subject,
            snippet=make_snippet(m.body_text or m.body_html or ""),
            folder=m.folder,
            is_read=m.is_read,
            is_starred=m.is_starred,
            is_deleted=m.is_deleted,
            received_at=m.received_at,
            attachments_count=int(att_map.get(m.id, 0)),
        )
        for m in messages
    ]
    return MessageList(items=items, page=page, limit=limit, total=total, pages=pages)


@router.get("/{message_id}", response_model=MessageDetail)
def get_message(
    message_id: UUID,
    mark_read: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = _user_message(db, user, message_id)
    if mark_read and not msg.is_read:
        msg.is_read = True
        db.flush()
        db.commit()
        db.refresh(msg)

    attachments = db.execute(
        select(EmailAttachment).where(EmailAttachment.email_message_id == msg.id)
    ).scalars().all()

    return MessageDetail(
        id=msg.id,
        mailbox_id=msg.mailbox_id,
        from_email=msg.from_email,
        from_name=msg.from_name,
        to_email=msg.to_email,
        subject=msg.subject,
        body_text=msg.body_text,
        body_html=sanitize_email_html(msg.body_html),
        folder=msg.folder,
        is_read=msg.is_read,
        is_starred=msg.is_starred,
        is_deleted=msg.is_deleted,
        received_at=msg.received_at,
        headers=msg.headers or {},
        size=msg.size,
        attachments=[AttachmentOut.model_validate(a) for a in attachments],
    )


@router.post("/read-all")
def read_all(
    payload: ReadAllIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    stmt = update(EmailMessage).where(
        EmailMessage.user_id == user.id, EmailMessage.is_read.is_(False)
    )
    if payload.folder:
        if payload.folder == "starred":
            stmt = stmt.where(EmailMessage.is_starred.is_(True))
        elif payload.folder == "trash":
            stmt = stmt.where(
                or_(EmailMessage.folder == "trash", EmailMessage.is_deleted.is_(True))
            )
        else:
            stmt = stmt.where(EmailMessage.folder == payload.folder)
    if payload.mailbox_id:
        stmt = stmt.where(EmailMessage.mailbox_id == payload.mailbox_id)
    stmt = stmt.values(is_read=True)
    result = db.execute(stmt)
    db.commit()
    return {"ok": True, "updated": int(result.rowcount or 0)}


@router.post("/bulk")
def bulk_action(
    payload: BulkActionIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not payload.message_ids:
        return {"ok": True, "updated": 0}
    base_filter = and_(
        EmailMessage.user_id == user.id, EmailMessage.id.in_(payload.message_ids)
    )

    if payload.action == "read":
        stmt = update(EmailMessage).where(base_filter).values(is_read=True)
    elif payload.action == "unread":
        stmt = update(EmailMessage).where(base_filter).values(is_read=False)
    elif payload.action == "star":
        stmt = update(EmailMessage).where(base_filter).values(is_starred=True)
    elif payload.action == "unstar":
        stmt = update(EmailMessage).where(base_filter).values(is_starred=False)
    elif payload.action == "archive":
        stmt = (
            update(EmailMessage)
            .where(base_filter)
            .values(folder="archive", is_deleted=False)
        )
    elif payload.action == "trash":
        stmt = (
            update(EmailMessage)
            .where(base_filter)
            .values(folder="trash", is_deleted=True)
        )
    elif payload.action == "inbox":
        stmt = (
            update(EmailMessage)
            .where(base_filter)
            .values(folder="inbox", is_deleted=False)
        )
    elif payload.action == "delete":
        # Only permanently delete those already in trash; others -> trash first.
        not_in_trash = (
            db.execute(
                select(EmailMessage.id).where(
                    base_filter,
                    EmailMessage.is_deleted.is_(False),
                    EmailMessage.folder != "trash",
                )
            )
            .scalars()
            .all()
        )
        if not_in_trash:
            db.execute(
                update(EmailMessage)
                .where(EmailMessage.id.in_(not_in_trash))
                .values(folder="trash", is_deleted=True)
            )
        # Now hard-delete those already in trash
        in_trash = (
            db.execute(
                select(EmailMessage.id).where(
                    base_filter,
                    or_(
                        EmailMessage.is_deleted.is_(True),
                        EmailMessage.folder == "trash",
                    ),
                )
            )
            .scalars()
            .all()
        )
        deleted_count = 0
        if in_trash:
            for chunk_start in range(0, len(in_trash), 200):
                ids = in_trash[chunk_start : chunk_start + 200]
                result = db.execute(
                    EmailMessage.__table__.delete().where(EmailMessage.id.in_(ids))
                )
                deleted_count += int(result.rowcount or 0)
        db.commit()
        return {"ok": True, "trashed": len(not_in_trash), "deleted": deleted_count}
    else:
        raise HTTPException(status_code=400, detail="Unknown action.")

    result = db.execute(stmt)
    db.commit()
    return {"ok": True, "updated": int(result.rowcount or 0)}


@router.post("/{message_id}/read")
def toggle_read(
    message_id: UUID,
    payload: ToggleReadIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = _user_message(db, user, message_id)
    msg.is_read = bool(payload.is_read)
    db.commit()
    return {"ok": True, "id": str(msg.id), "is_read": msg.is_read}


@router.post("/{message_id}/star")
def toggle_star(
    message_id: UUID,
    payload: ToggleStarIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = _user_message(db, user, message_id)
    msg.is_starred = bool(payload.is_starred)
    db.commit()
    return {"ok": True, "id": str(msg.id), "is_starred": msg.is_starred}


@router.post("/{message_id}/archive")
def archive_message(
    message_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = _user_message(db, user, message_id)
    msg.folder = "archive"
    msg.is_deleted = False
    db.commit()
    return {"ok": True, "id": str(msg.id), "folder": msg.folder}


@router.post("/{message_id}/inbox")
def move_to_inbox(
    message_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = _user_message(db, user, message_id)
    msg.folder = "inbox"
    msg.is_deleted = False
    db.commit()
    return {"ok": True, "id": str(msg.id), "folder": msg.folder}


@router.post("/{message_id}/trash")
def move_to_trash(
    message_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = _user_message(db, user, message_id)
    msg.folder = "trash"
    msg.is_deleted = True
    db.commit()
    return {"ok": True, "id": str(msg.id), "folder": msg.folder}


@router.delete("/{message_id}")
def delete_message(
    message_id: UUID,
    force: bool = Query(default=False),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = _user_message(db, user, message_id)
    in_trash = msg.folder == "trash" or msg.is_deleted
    if not in_trash and not force:
        msg.folder = "trash"
        msg.is_deleted = True
        db.commit()
        return {"ok": True, "id": str(msg.id), "moved_to": "trash"}
    db.delete(msg)
    db.commit()
    log_event(
        db,
        action="message.delete",
        user_id=user.id,
        metadata={"message_id": str(message_id)},
        commit=True,
    )
    return {"ok": True, "id": str(message_id), "deleted": True}
