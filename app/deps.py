"""FastAPI dependencies (auth, role check)."""

from __future__ import annotations

from typing import Optional

from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .auth import decode_session_token
from .config import settings
from .database import get_db
from .models import User


def _bearer_from_header(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def get_current_user_optional(
    db: Session = Depends(get_db),
    softrise_session: Optional[str] = Cookie(default=None, alias=settings.SESSION_COOKIE_NAME),
    authorization: Optional[str] = Header(default=None),
) -> Optional[User]:
    """Resolve the current user from cookie or Bearer header without raising."""
    token = softrise_session or _bearer_from_header(authorization)
    if not token:
        return None
    user_id = decode_session_token(token)
    if not user_id:
        return None
    user = db.get(User, user_id)
    if not user or not user.is_active:
        return None
    return user


def get_current_user(
    user: Optional[User] = Depends(get_current_user_optional),
) -> User:
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def get_current_admin(
    user: User = Depends(get_current_user),
) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required.",
        )
    return user
