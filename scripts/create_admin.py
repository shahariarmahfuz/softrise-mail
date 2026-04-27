"""Create or promote a user to admin role.

Usage:
    python -m scripts.create_admin <username> [--password <pw>] [--email <em>] [--name <name>]

If the user already exists, only the role is changed to admin.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import func, select

from app.auth import create_default_mailbox_for, hash_password
from app.database import SessionLocal
from app.models import Mailbox, User


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("username")
    ap.add_argument("--password", default=None, help="Password for new admin user")
    ap.add_argument("--email", default=None)
    ap.add_argument("--name", default=None)
    args = ap.parse_args()

    db = SessionLocal()
    try:
        user = db.execute(
            select(User).where(func.lower(User.username) == args.username.lower())
        ).scalars().first()
        if user:
            user.role = "admin"
            user.is_active = True
            db.commit()
            print(f"Promoted existing user '{user.username}' to admin.")
            return 0

        if not args.password:
            print("New user requires --password", file=sys.stderr)
            return 2
        user = User(
            username=args.username,
            email=args.email,
            name=args.name or args.username,
            password_hash=hash_password(args.password),
            role="admin",
            is_active=True,
            settings={},
        )
        db.add(user)
        db.flush()
        # Auto-provision default mailbox
        existing_default = db.execute(
            select(Mailbox).where(Mailbox.user_id == user.id, Mailbox.is_default.is_(True))
        ).scalars().first()
        if not existing_default:
            create_default_mailbox_for(db, user)
        db.commit()
        print(f"Created admin user '{user.username}'.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
