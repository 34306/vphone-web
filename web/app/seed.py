"""Create or update an admin user.

Usage: python -m web.app.seed --user admin --pass secret [--role admin]
"""
import argparse

from .auth import hash_password
from .db import SessionLocal, init_db
from .models import User


def seed_admin(username: str, password: str, role: str = "admin") -> str:
    init_db()
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            user = User(username=username, password_hash=hash_password(password), role=role)
            db.add(user)
            action = "created"
        else:
            user.password_hash = hash_password(password)
            user.role = role
            action = "updated"
        db.commit()
        return action
    finally:
        db.close()


def main() -> None:
    p = argparse.ArgumentParser(description="Seed a vphone admin account")
    p.add_argument("--user", required=True)
    p.add_argument("--pass", dest="password", required=True)
    p.add_argument("--role", default="admin", choices=["admin", "user"])
    args = p.parse_args()
    action = seed_admin(args.user, args.password, args.role)
    print(f"{action} {args.role} user {args.user!r}")


if __name__ == "__main__":
    main()
