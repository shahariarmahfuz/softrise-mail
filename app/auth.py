"""Authentication helpers: password hashing, JWT, mailbox auto-provisioning."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Mailbox, User
from .utils import (
    is_valid_localpart,
    random_token,
    slugify_localpart,
)

logger = logging.getLogger(__name__)

# bcrypt with a sane cost factor.  The bcrypt backend caps at 72 bytes,
# so we pass a `truncate_error=False` style by relying on passlib defaults
# which silently truncate (ok for password use-case).
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

JWT_ALGORITHM = "HS256"


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _pwd_context.verify(password, password_hash)
    except Exception:
        # Mismatched scheme, malformed hash, etc.
        return False


def create_session_token(user_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": str(user_id),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=settings.SESSION_TTL_HOURS)).timestamp()),
        "type": "session",
    }
    return jwt.encode(payload, settings.APP_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_session_token(token: str) -> Optional[str]:
    """Return user_id (sub) on success, None on failure/expiry."""
    if not token:
        return None
    try:
        data = jwt.decode(token, settings.APP_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
    if data.get("type") != "session":
        return None
    return data.get("sub")


def find_available_localpart(
    db: Session,
    desired: str,
    *,
    domain: str,
    fallback_seed: str = "user",
    max_tries: int = 8,
) -> str:
    """Return a localpart that is currently free among ACTIVE mailboxes.

    We respect the partial-unique index (active mailboxes only) at the DB level,
    but we'd rather pick a free name up-front to give the user a clean default
    address.
    """
    desired_clean = slugify_localpart(desired)
    if not is_valid_localpart(desired_clean):
        desired_clean = slugify_localpart(fallback_seed) or "user"
    if not is_valid_localpart(desired_clean):
        desired_clean = "user"

    candidate = desired_clean
    for attempt in range(max_tries):
        full = f"{candidate}@{domain}"
        # Check active mailbox uniqueness
        existing = (
            db.execute(
                select(Mailbox.id).where(
                    func.lower(Mailbox.email_address) == full.lower(),
                    Mailbox.deleted_at.is_(None),
                )
            )
            .scalars()
            .first()
        )
        if existing is None:
            return candidate
        suffix = random_token(4 if attempt < 4 else 6)
        candidate = f"{desired_clean}.{suffix}"[:64]

    # Last-ditch fallback
    return f"{desired_clean}.{random_token(8)}"[:64]


def create_default_mailbox_for(
    db: Session,
    user: User,
    *,
    desired_local_part: Optional[str] = None,
) -> Mailbox:
    """Create the user's default mailbox (``localpart@APP_DOMAIN``)."""
    domain = settings.APP_DOMAIN
    seed = desired_local_part or user.username or "user"
    local_part = find_available_localpart(
        db, seed, domain=domain, fallback_seed=user.username or "user"
    )
    full = f"{local_part}@{domain}"
    mailbox = Mailbox(
        user_id=user.id,
        email_address=full,
        local_part=local_part,
        domain=domain,
        type="default",
        is_default=True,
        is_active=True,
    )
    db.add(mailbox)
    db.flush()
    return mailbox


def find_user_by_login(db: Session, identifier: str) -> Optional[User]:
    """Look up a user by username OR email (case-insensitive)."""
    if not identifier:
        return None
    needle = identifier.strip().lower()
    return (
        db.execute(
            select(User).where(
                (func.lower(User.username) == needle) | (func.lower(User.email) == needle)
            )
        )
        .scalars()
        .first()
    )
