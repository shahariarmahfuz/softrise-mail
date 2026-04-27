"""Cloudflare Email Worker -> /webhook/email receiver.

This endpoint is intentionally PUBLIC: the Cloudflare Email Worker posts the
parsed message with no authentication header, so we must not gate this route
behind ``WEBHOOK_SECRET``, JWT, or session cookies.

Worker payload contract::

    POST /webhook/email
    Content-Type: application/json

    {
      "from":      "<envelope sender>",
      "to":        "<envelope recipient>",
      "size":      <int>,
      "headers":   { "<header>": "<value>", ... },
      "raw_email": "<full RFC822 message>"
    }

Responses:
- 200 ``{"ok": true,  "stored": true,  "message_id": "<uuid>"}``
- 202 ``{"ok": true,  "stored": false, "reason": "mailbox_not_found"}``
- 4xx for malformed JSON / oversized payload / missing recipient.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..audit import log_event
from ..config import settings
from ..database import SessionLocal
from ..email_parser import parse_raw_email
from ..models import EmailAttachment, EmailMessage, Mailbox
from ..utils import sanitize_filename

logger = logging.getLogger("softrise.webhook")

router = APIRouter(prefix="/webhook", tags=["webhook"])


def _store_attachment(message_id: str, payload, max_bytes: int) -> Path | None:
    """Write attachment bytes to disk if within size limit. Return path."""
    if not payload.data or len(payload.data) > max_bytes:
        return None
    safe_name = sanitize_filename(payload.filename)
    target_dir = settings.attachment_dir / message_id
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / safe_name
    counter = 1
    while target_path.exists():
        stem = target_path.stem
        suffix = target_path.suffix
        target_path = target_dir / f"{stem}_{counter}{suffix}"
        counter += 1
        if counter > 100:
            return None
    try:
        target_path.write_bytes(payload.data)
        return target_path
    except OSError as exc:
        logger.warning("Failed to write attachment %s: %s", safe_name, exc)
        return None


def _client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.post("/email")
async def receive_email(request: Request):
    """Receive an inbound email from the Cloudflare Email Worker.

    Public endpoint — no auth, no secret header required. The Worker only
    sends ``Content-Type: application/json`` and we must accept that exact
    payload shape unchanged.
    """
    logger.info("[webhook] received POST /webhook/email from %s", _client_ip(request))

    # 1. Reject only on hard limits — never on missing auth.
    max_payload = settings.MAX_WEBHOOK_PAYLOAD_MB * 1024 * 1024
    body = await request.body()
    if len(body) > max_payload:
        logger.warning(
            "[webhook] payload too large: %d bytes (limit %d)",
            len(body),
            max_payload,
        )
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"ok": False, "stored": False, "reason": "payload_too_large"},
        )

    # 2. Parse JSON.
    try:
        data: dict[str, Any] = await request.json()
    except Exception as exc:
        logger.warning("[webhook] invalid JSON: %s", exc)
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"ok": False, "stored": False, "reason": "invalid_json"},
        )
    if not isinstance(data, dict):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"ok": False, "stored": False, "reason": "invalid_payload"},
        )

    # 3. Pull out exactly the fields the Worker sends.
    raw_email = data.get("raw_email") or ""
    fallback_to = data.get("to")
    fallback_from = data.get("from")
    fallback_headers = data.get("headers") if isinstance(data.get("headers"), dict) else None
    declared_size = int(data.get("size") or 0)

    parsed = parse_raw_email(
        raw_email,
        fallback_to=fallback_to,
        fallback_from=fallback_from,
        fallback_headers=fallback_headers,
    )
    if declared_size > parsed.size:
        parsed.size = declared_size

    # Determine the primary recipient (lowercased, trimmed) early — we want it
    # in the logs even if delivery later fails.
    recipients: list[str] = []
    for r in parsed.to_emails:
        if isinstance(r, str) and r.strip():
            recipients.append(r.strip().lower())
    if not recipients and isinstance(fallback_to, str) and fallback_to.strip():
        recipients.append(fallback_to.strip().lower())

    primary_recipient = recipients[0] if recipients else None
    logger.info(
        "[webhook] payload parsed: from=%s to=%s size=%d recipients=%s",
        parsed.from_email,
        primary_recipient,
        parsed.size,
        recipients,
    )

    # 4. Open a DB session manually (no Depends — we want full control over
    # commit/rollback for this public endpoint).
    db: Session = SessionLocal()
    try:
        if not recipients:
            logger.warning("[webhook] no recipient on payload; discarding")
            log_event(
                db,
                action="webhook.email.no_recipient",
                metadata={
                    "from": parsed.from_email,
                    "headers_keys": list((parsed.headers or {}).keys()),
                },
                commit=True,
            )
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={"ok": True, "stored": False, "reason": "no_recipient"},
            )

        # 5. Try each recipient. The Worker normally sends one, but message.to
        # can be a list — handle both shapes the parser already gives us.
        delivered_results: list[dict[str, Any]] = []
        first_stored_id: str | None = None

        for recipient_lc in recipients:
            mailbox = (
                db.execute(
                    select(Mailbox).where(
                        func.lower(Mailbox.email_address) == recipient_lc,
                        Mailbox.deleted_at.is_(None),
                    )
                )
                .scalars()
                .first()
            )
            if not mailbox:
                logger.info(
                    "[webhook] mailbox NOT FOUND for recipient=%s",
                    recipient_lc,
                )
                log_event(
                    db,
                    action="webhook.email.unknown_recipient",
                    metadata={"recipient": recipient_lc, "from": parsed.from_email},
                    commit=True,
                )
                delivered_results.append(
                    {"to": recipient_lc, "stored": False, "reason": "mailbox_not_found"}
                )
                continue

            logger.info(
                "[webhook] mailbox FOUND for recipient=%s mailbox_id=%s user_id=%s",
                recipient_lc,
                mailbox.id,
                mailbox.user_id,
            )

            msg = EmailMessage(
                mailbox_id=mailbox.id,
                user_id=mailbox.user_id,
                message_id=parsed.message_id,
                from_email=parsed.from_email,
                from_name=parsed.from_name,
                to_email=recipient_lc,
                subject=parsed.subject,
                body_text=parsed.body_text,
                body_html=parsed.body_html,
                raw_email=raw_email,
                headers=parsed.headers or {},
                size=parsed.size,
                folder="inbox",
                is_read=False,
                is_starred=False,
                is_deleted=False,
                received_at=parsed.received_at,
            )
            db.add(msg)
            db.flush()

            max_attachment_bytes = settings.MAX_ATTACHMENT_SIZE_MB * 1024 * 1024
            for payload in parsed.attachments:
                stored_path = _store_attachment(str(msg.id), payload, max_attachment_bytes)
                db.add(
                    EmailAttachment(
                        email_message_id=msg.id,
                        filename=sanitize_filename(payload.filename),
                        content_type=payload.content_type,
                        size=payload.size,
                        storage_path=str(stored_path) if stored_path else None,
                    )
                )

            log_event(
                db,
                action="webhook.email.delivered",
                user_id=mailbox.user_id,
                metadata={
                    "mailbox": mailbox.email_address,
                    "from": parsed.from_email,
                    "subject": parsed.subject,
                    "message_db_id": str(msg.id),
                },
            )
            stored_id = str(msg.id)
            if first_stored_id is None:
                first_stored_id = stored_id
            delivered_results.append(
                {
                    "to": recipient_lc,
                    "stored": True,
                    "message_id": stored_id,
                    "mailbox_id": str(mailbox.id),
                }
            )
            logger.info(
                "[webhook] STORED message_id=%s for mailbox=%s",
                stored_id,
                mailbox.email_address,
            )

        db.commit()

        # 6. Build response in the exact shape the requirements ask for.
        if first_stored_id is not None:
            return JSONResponse(
                status_code=status.HTTP_200_OK,
                content={
                    "ok": True,
                    "stored": True,
                    "message_id": first_stored_id,
                    "deliveries": delivered_results,
                },
            )

        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "ok": True,
                "stored": False,
                "reason": "mailbox_not_found",
                "deliveries": delivered_results,
            },
        )
    except Exception as exc:
        logger.exception("[webhook] unhandled error: %s", exc)
        db.rollback()
        # Never 401/500 the Worker — Cloudflare retries, so prefer 202 to
        # acknowledge receipt while we investigate.
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={"ok": False, "stored": False, "reason": "internal_error"},
        )
    finally:
        db.close()
