"""Pydantic models for request/response payloads."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

# ---------- Auth ----------


class RegisterIn(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    username: str = Field(..., min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    password: str = Field(..., min_length=6, max_length=128)

    @field_validator("username")
    @classmethod
    def _username_chars(cls, v: str) -> str:
        v = v.strip()
        if not v.replace(".", "").replace("_", "").replace("-", "").isalnum():
            raise ValueError(
                "Username may only contain letters, digits, dots, dashes and underscores."
            )
        return v


class LoginIn(BaseModel):
    identifier: str = Field(..., min_length=2, max_length=255)
    password: str = Field(..., min_length=1, max_length=128)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: Optional[str]
    username: str
    email: Optional[str]
    role: str
    is_active: bool
    created_at: datetime


class MeOut(UserOut):
    default_mailbox: Optional["MailboxOut"] = None
    settings: dict[str, Any] = {}


# ---------- Mailbox ----------


class MailboxOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email_address: str
    local_part: str
    domain: str
    type: str
    is_default: bool
    is_active: bool
    deleted_at: Optional[datetime] = None
    created_at: datetime


class CreateTempMailboxIn(BaseModel):
    local_part: Optional[str] = Field(None, max_length=64)


class CheckLocalPartOut(BaseModel):
    available: bool
    email: str
    reason: Optional[str] = None


# ---------- Messages ----------


class AttachmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    filename: str
    content_type: Optional[str]
    size: int


class MessageListItem(BaseModel):
    id: UUID
    mailbox_id: UUID
    from_email: str
    from_name: Optional[str]
    to_email: str
    subject: Optional[str]
    snippet: str
    folder: str
    is_read: bool
    is_starred: bool
    is_deleted: bool
    received_at: datetime
    attachments_count: int = 0


class MessageDetail(BaseModel):
    id: UUID
    mailbox_id: UUID
    from_email: str
    from_name: Optional[str]
    to_email: str
    subject: Optional[str]
    body_text: Optional[str]
    body_html: Optional[str]
    folder: str
    is_read: bool
    is_starred: bool
    is_deleted: bool
    received_at: datetime
    headers: dict[str, Any]
    size: int
    attachments: list[AttachmentOut]


class MessageList(BaseModel):
    items: list[MessageListItem]
    page: int
    limit: int
    total: int
    pages: int


class ToggleReadIn(BaseModel):
    is_read: bool


class ToggleStarIn(BaseModel):
    is_starred: bool


class ReadAllIn(BaseModel):
    folder: Optional[str] = None
    mailbox_id: Optional[UUID] = None


class BulkActionIn(BaseModel):
    message_ids: list[UUID]
    action: Literal[
        "read", "unread", "star", "unstar", "archive", "trash", "delete", "inbox"
    ]


# ---------- Webhook ----------


class WebhookEmailIn(BaseModel):
    model_config = ConfigDict(extra="allow")

    from_: Optional[str] = Field(None, alias="from")
    to: Optional[Any] = None
    size: Optional[int] = None
    headers: Optional[dict[str, Any]] = None
    raw_email: str = ""


# ---------- Settings ----------


class UserSettingsIn(BaseModel):
    display_name: Optional[str] = Field(None, max_length=100)
    default_mailbox_id: Optional[UUID] = None
    emails_per_page: Optional[int] = Field(None, ge=5, le=100)
    theme: Optional[str] = None
    extra: Optional[dict[str, Any]] = None


# ---------- Admin ----------


class AdminUserListItem(BaseModel):
    id: UUID
    name: Optional[str]
    username: str
    email: Optional[str]
    role: str
    is_active: bool
    created_at: datetime
    mailbox_count: int = 0
    message_count: int = 0


class AdminUserUpdateIn(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    role: Optional[Literal["user", "admin"]] = None
    is_active: Optional[bool] = None


class AdminMailboxCreateIn(BaseModel):
    local_part: str
    type: Literal["default", "temp"] = "temp"


class AdminSettingsIn(BaseModel):
    settings: dict[str, Any]


class AuditLogOut(BaseModel):
    id: UUID
    user_id: Optional[UUID]
    action: str
    metadata: dict[str, Any]
    created_at: datetime


# Resolve forward refs
MeOut.model_rebuild()
