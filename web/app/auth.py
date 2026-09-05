"""Password hashing, session cookies, and auth dependencies."""
from fastapi import Cookie, Depends, HTTPException, status
from itsdangerous import BadSignature, URLSafeTimedSerializer
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from . import config
from .db import get_db
from .models import User

COOKIE_NAME = "vphone_session"
MAX_AGE = 60 * 60 * 24 * 7  # 7 days

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
_serializer = URLSafeTimedSerializer(config.get_secret_key(), salt="vphone-session")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return _pwd.verify(password, password_hash)


def make_session_token(user_id: int) -> str:
    return _serializer.dumps({"uid": user_id})


def _read_session(token: str | None) -> int | None:
    if not token:
        return None
    try:
        data = _serializer.loads(token, max_age=MAX_AGE)
        return int(data["uid"])
    except (BadSignature, KeyError, ValueError):
        return None


def get_current_user(
    vphone_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    uid = _read_session(vphone_session)
    if uid is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    user = db.get(User, uid)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid session")
    if user.is_expired:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="account access has expired — contact an admin")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin only")
    return user
