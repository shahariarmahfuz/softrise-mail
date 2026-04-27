"""Generic helpers: localpart validation, slugify, secure random, sanitization."""

from __future__ import annotations

import os
import re
import secrets
import string
from typing import Iterable, Optional

import bleach
from bleach.css_sanitizer import CSSSanitizer

# Allowed in localpart: lowercase letters, digits, dot, dash, underscore.
LOCALPART_RE = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?$")
SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]")

ALLOWED_HTML_TAGS = list(bleach.sanitizer.ALLOWED_TAGS) + [
    "p",
    "div",
    "span",
    "br",
    "hr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "blockquote",
    "pre",
    "code",
    "table",
    "thead",
    "tbody",
    "tfoot",
    "tr",
    "th",
    "td",
    "img",
    "figure",
    "figcaption",
    "small",
    "u",
    "s",
    "sub",
    "sup",
]
ALLOWED_HTML_ATTRS = {
    "*": ["class", "style", "id", "align", "title"],
    "a": ["href", "name", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height"],
    "td": ["colspan", "rowspan", "width", "height"],
    "th": ["colspan", "rowspan", "width", "height"],
    "table": ["border", "cellpadding", "cellspacing", "width"],
}
ALLOWED_HTML_PROTOCOLS = ["http", "https", "mailto", "cid"]


def slugify_localpart(value: str) -> str:
    """Normalize an arbitrary string into a valid localpart."""
    if not value:
        return ""
    value = value.strip().lower()
    # Replace spaces and consecutive separators with a dot
    value = re.sub(r"\s+", ".", value)
    # Strip out anything that isn't allowed
    value = re.sub(r"[^a-z0-9._-]", "", value)
    # Collapse repeated dots / dashes / underscores
    value = re.sub(r"[._-]{2,}", ".", value)
    value = value.strip("._-")
    return value[:64]


def is_valid_localpart(value: str) -> bool:
    if not value or len(value) > 64:
        return False
    return bool(LOCALPART_RE.match(value))


def random_token(length: int = 16) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def random_localpart(prefix: str = "temp") -> str:
    """Generate a random temp mailbox localpart, e.g. ``temp.k7d9aq``."""
    base = slugify_localpart(prefix) or "temp"
    return f"{base}.{random_token(8)}"[:64]


def sanitize_filename(filename: Optional[str], default: str = "attachment.bin") -> str:
    if not filename:
        return default
    # Drop path components and dangerous characters.
    name = os.path.basename(filename)
    name = SAFE_FILENAME_RE.sub("_", name)
    name = name.strip("._")
    return (name or default)[:200]


_DANGEROUS_TAG_RE = re.compile(
    r"<(script|style|iframe|object|embed|form)[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_DANGEROUS_SELFCLOSE_RE = re.compile(
    r"<(script|style|iframe|object|embed|form|link|meta)[^>]*/?>",
    re.IGNORECASE,
)

_CSS_SANITIZER = CSSSanitizer(
    allowed_css_properties=[
        "background", "background-color", "border", "border-color",
        "border-radius", "border-style", "border-width", "color",
        "display", "font", "font-family", "font-size", "font-style",
        "font-weight", "height", "letter-spacing", "line-height",
        "list-style", "margin", "margin-bottom", "margin-left",
        "margin-right", "margin-top", "max-height", "max-width",
        "min-height", "min-width", "opacity", "padding", "padding-bottom",
        "padding-left", "padding-right", "padding-top", "text-align",
        "text-decoration", "text-indent", "text-transform", "vertical-align",
        "white-space", "width", "word-break", "word-wrap",
    ]
)


def sanitize_email_html(html: Optional[str]) -> Optional[str]:
    """Render-safe HTML for displaying emails."""
    if not html:
        return html
    # Drop entire dangerous elements (with their text content) before bleach
    cleaned = _DANGEROUS_TAG_RE.sub("", html)
    cleaned = _DANGEROUS_SELFCLOSE_RE.sub("", cleaned)
    cleaner = bleach.Cleaner(
        tags=ALLOWED_HTML_TAGS,
        attributes=ALLOWED_HTML_ATTRS,
        protocols=ALLOWED_HTML_PROTOCOLS,
        css_sanitizer=_CSS_SANITIZER,
        strip=True,
        strip_comments=True,
    )
    return cleaner.clean(cleaned)


def make_snippet(text_or_html: Optional[str], max_len: int = 180) -> str:
    if not text_or_html:
        return ""
    # Strip HTML tags if any
    cleaned = re.sub(r"<[^>]+>", " ", text_or_html)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - 1].rstrip() + "\u2026"


def email_or_none(value: Optional[str]) -> Optional[str]:
    """Lower-case + strip an email-ish value, returning None if empty."""
    if not value:
        return None
    out = value.strip().lower()
    return out or None


def chunked(seq: Iterable, size: int):
    chunk = []
    for item in seq:
        chunk.append(item)
        if len(chunk) >= size:
            yield chunk
            chunk = []
    if chunk:
        yield chunk
