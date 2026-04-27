"""Parse raw RFC822 email content into structured fields + attachment payloads."""

from __future__ import annotations

import email
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.header import decode_header, make_header
from email.message import Message
from email.utils import parsedate_to_datetime, parseaddr, getaddresses
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class AttachmentPayload:
    filename: str
    content_type: Optional[str]
    size: int
    data: bytes
    is_inline: bool = False
    content_id: Optional[str] = None


@dataclass
class ParsedEmail:
    message_id: Optional[str] = None
    subject: Optional[str] = None
    from_email: str = ""
    from_name: Optional[str] = None
    to_emails: list[str] = field(default_factory=list)
    received_at: Optional[datetime] = None
    body_text: Optional[str] = None
    body_html: Optional[str] = None
    headers: dict[str, str] = field(default_factory=dict)
    attachments: list[AttachmentPayload] = field(default_factory=list)
    size: int = 0


def _decode(value: Optional[str]) -> Optional[str]:
    """Decode an RFC2047-encoded header into a plain Unicode string."""
    if value is None:
        return None
    try:
        return str(make_header(decode_header(value))).strip() or None
    except Exception:
        # Fall back: best-effort string
        return value.strip() or None


def _decode_payload(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    if isinstance(payload, bytes):
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, errors="replace")
        except (LookupError, TypeError):
            return payload.decode("utf-8", errors="replace")
    return str(payload)


def _is_attachment(part: Message) -> bool:
    cd = (part.get("Content-Disposition") or "").lower()
    if "attachment" in cd:
        return True
    filename = part.get_filename()
    if filename:
        return True
    # Inline image with Content-ID is treated as inline attachment
    if (part.get("Content-Disposition") or "").lower().startswith("inline") and part.get("Content-ID"):
        return True
    return False


def _strip_html(html: str) -> str:
    text = re.sub(r"<style[\s\S]*?</style>", " ", html, flags=re.IGNORECASE)
    text = re.sub(r"<script[\s\S]*?</script>", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def parse_raw_email(
    raw_email: str | bytes,
    *,
    fallback_to: Optional[str] = None,
    fallback_from: Optional[str] = None,
    fallback_headers: Optional[dict[str, Any]] = None,
) -> ParsedEmail:
    """Parse a raw email blob into a structured ``ParsedEmail``.

    Falls back to webhook-supplied ``to`` / ``from`` / ``headers`` if the
    raw content is missing or unparseable.
    """
    if isinstance(raw_email, bytes):
        raw_bytes = raw_email
    else:
        raw_bytes = (raw_email or "").encode("utf-8", errors="replace")

    parsed = ParsedEmail(size=len(raw_bytes))

    msg: Optional[Message] = None
    if raw_bytes:
        try:
            msg = email.message_from_bytes(raw_bytes)
        except Exception as exc:
            logger.warning("Failed to parse raw email: %s", exc)
            msg = None

    # 1. Headers ------------------------------------------------------------
    if msg is not None:
        headers: dict[str, str] = {}
        for k, v in msg.items():
            decoded = _decode(v) or ""
            # When duplicates exist, later values overwrite earlier (good enough)
            headers[k] = decoded
        parsed.headers = headers
    elif fallback_headers:
        parsed.headers = {str(k): str(v) for k, v in fallback_headers.items()}

    # 2. Subject ------------------------------------------------------------
    if msg is not None:
        parsed.subject = _decode(msg.get("Subject"))
    if not parsed.subject and fallback_headers:
        # Cloudflare Worker sends headers map (case may vary)
        for k, v in (fallback_headers or {}).items():
            if str(k).lower() == "subject":
                parsed.subject = _decode(str(v))
                break

    # 3. Message-Id ---------------------------------------------------------
    if msg is not None:
        parsed.message_id = _decode(msg.get("Message-Id") or msg.get("Message-ID"))

    # 4. From ---------------------------------------------------------------
    from_raw = (msg.get("From") if msg else None) or fallback_from or ""
    name, addr = parseaddr(from_raw or "")
    if not addr and fallback_from:
        _, addr = parseaddr(fallback_from)
        name = name or ""
    parsed.from_email = (addr or "").strip().lower()
    parsed.from_name = _decode(name) if name else None

    # 5. To -----------------------------------------------------------------
    to_addrs: list[str] = []
    if msg is not None:
        for header_name in ("To", "Delivered-To", "X-Original-To", "X-Forwarded-To"):
            for _name, addr in getaddresses(msg.get_all(header_name) or []):
                if addr:
                    to_addrs.append(addr.strip().lower())

    # Webhook-supplied "to" wins/augments. It can be a string or list.
    if fallback_to:
        if isinstance(fallback_to, list):
            for v in fallback_to:
                if v:
                    to_addrs.append(str(v).strip().lower())
        else:
            to_addrs.append(str(fallback_to).strip().lower())

    # De-dup while preserving order
    seen = set()
    parsed.to_emails = [a for a in to_addrs if a and (a not in seen and not seen.add(a))]

    # 6. Date / received_at -------------------------------------------------
    if msg is not None:
        date_hdr = msg.get("Date")
        if date_hdr:
            try:
                d = parsedate_to_datetime(date_hdr)
                if d.tzinfo is None:
                    d = d.replace(tzinfo=timezone.utc)
                parsed.received_at = d
            except Exception:
                parsed.received_at = None
    if parsed.received_at is None:
        parsed.received_at = datetime.now(timezone.utc)

    # 7. Bodies + attachments ----------------------------------------------
    text_parts: list[str] = []
    html_parts: list[str] = []
    if msg is not None:
        if msg.is_multipart():
            for part in msg.walk():
                if part.is_multipart():
                    continue
                if _is_attachment(part):
                    payload = part.get_payload(decode=True) or b""
                    parsed.attachments.append(
                        AttachmentPayload(
                            filename=_decode(part.get_filename()) or "attachment.bin",
                            content_type=part.get_content_type(),
                            size=len(payload),
                            data=payload,
                            is_inline="inline" in (part.get("Content-Disposition") or "").lower(),
                            content_id=(part.get("Content-ID") or "").strip("<>") or None,
                        )
                    )
                    continue
                ctype = part.get_content_type()
                if ctype == "text/plain":
                    text_parts.append(_decode_payload(part))
                elif ctype == "text/html":
                    html_parts.append(_decode_payload(part))
        else:
            ctype = msg.get_content_type()
            content = _decode_payload(msg)
            if ctype == "text/html":
                html_parts.append(content)
            else:
                text_parts.append(content)

    parsed.body_text = ("\n\n".join(p for p in text_parts if p)).strip() or None
    parsed.body_html = ("\n\n".join(p for p in html_parts if p)).strip() or None

    # If we only have HTML body, derive a plain-text representation for the snippet.
    if not parsed.body_text and parsed.body_html:
        parsed.body_text = _strip_html(parsed.body_html) or None

    return parsed
