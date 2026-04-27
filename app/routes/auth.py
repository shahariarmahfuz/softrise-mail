"""User authentication endpoints (register / login / logout / me)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..audit import log_event
from ..auth import (
    create_default_mailbox_for,
    create_session_token,
    find_user_by_login,
    hash_password,
    verify_password,
)
from ..config import settings
from ..database import get_db
from ..deps import get_current_user
from ..models import Mailbox, User
from ..schemas import LoginIn, MailboxOut, MeOut, RegisterIn, UserOut
from ..utils import slugify_localpart

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _is_https_request(request: Request) -> bool:
    """Detect whether the request reached us over HTTPS.

    Honors `X-Forwarded-Proto` when uvicorn is launched with proxy_headers=True
    (which is the case in app.py).  This means:
    - http://127.0.0.1:5000 / http://server-ip:5000   -> False
    - https://mail.softrise.app                       -> True (via proxy)
    """
    scheme = request.url.scheme.lower() if request and request.url else "http"
    return scheme == "https"


def _set_session_cookie(request: Request, response: Response, token: str) -> None:
    """Set the session cookie with the right Secure flag for the current scheme.

    On HTTP (dev / running directly on an IP / localhost), Secure MUST be
    False or browsers will silently drop the cookie.  On HTTPS production we
    flip it on automatically.
    """
    secure = _is_https_request(request) or settings.is_production
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        max_age=settings.SESSION_TTL_HOURS * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/",
    )


def _clear_session_cookie(request: Request, response: Response) -> None:
    secure = _is_https_request(request) or settings.is_production
    # Match the attributes used when setting so the browser actually deletes it.
    response.delete_cookie(
        key=settings.SESSION_COOKIE_NAME,
        path="/",
        samesite="lax",
        secure=secure,
        httponly=True,
    )


def _build_me(db: Session, user: User) -> MeOut:
    default_mailbox = (
        db.execute(
            select(Mailbox)
            .where(
                Mailbox.user_id == user.id,
                Mailbox.is_default.is_(True),
                Mailbox.deleted_at.is_(None),
            )
            .limit(1)
        )
        .scalars()
        .first()
    )
    return MeOut(
        id=user.id,
        name=user.name,
        username=user.username,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        created_at=user.created_at,
        default_mailbox=MailboxOut.model_validate(default_mailbox) if default_mailbox else None,
        settings=user.settings or {},
    )


@router.post("/register", response_model=MeOut, status_code=status.HTTP_201_CREATED)
def register(
    payload: RegisterIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    username = payload.username.strip()
    if (
        db.execute(
            select(User.id).where(func.lower(User.username) == username.lower())
        )
        .scalars()
        .first()
    ):
        raise HTTPException(status_code=409, detail="Username already taken.")
    if payload.email:
        email_lc = payload.email.lower()
        if (
            db.execute(select(User.id).where(func.lower(User.email) == email_lc))
            .scalars()
            .first()
        ):
            raise HTTPException(status_code=409, detail="Email already registered.")

    user = User(
        name=(payload.name or "").strip(),
        username=username,
        email=payload.email.lower() if payload.email else None,
        password_hash=hash_password(payload.password),
        role="user",
        settings={},
    )
    db.add(user)
    db.flush()  # populate user.id

    # Auto-create the user's default @softrise.app mailbox.
    desired = slugify_localpart(payload.username) or "user"
    try:
        mailbox = create_default_mailbox_for(db, user, desired_local_part=desired)
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Could not provision a default mailbox. Please retry.",
        )

    log_event(
        db,
        action="user.register",
        user_id=user.id,
        metadata={
            "username": user.username,
            "default_mailbox": mailbox.email_address,
        },
    )
    db.commit()
    db.refresh(user)
    db.refresh(mailbox)

    token = create_session_token(str(user.id))
    _set_session_cookie(request, response, token)
    return _build_me(db, user)


@router.post("/login", response_model=MeOut)
def login(
    payload: LoginIn,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    user = find_user_by_login(db, payload.identifier)
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        # Record failure (best-effort, no row if user unknown)
        log_event(
            db,
            action="user.login_failed",
            user_id=getattr(user, "id", None),
            metadata={"identifier": payload.identifier},
            commit=True,
        )
        raise HTTPException(status_code=401, detail="Invalid credentials.")

    log_event(
        db,
        action="user.login",
        user_id=user.id,
        metadata={"username": user.username},
        commit=True,
    )
    token = create_session_token(str(user.id))
    _set_session_cookie(request, response, token)
    return _build_me(db, user)


@router.post("/logout")
def logout(request: Request, response: Response):
    _clear_session_cookie(request, response)
    return {"ok": True}


@router.get("/me", response_model=MeOut)
def me(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _build_me(db, user)
