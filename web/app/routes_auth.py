"""Login / logout (with brute-force rate limiting)."""
import time
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, HTTPException, Request, Response
from sqlalchemy.orm import Session

from . import config
from .auth import COOKIE_NAME, MAX_AGE, make_session_token, verify_password
from .db import get_db
from .models import User

router = APIRouter(prefix="/api", tags=["auth"])

# --- login brute-force throttle (per client IP) ---
_FAIL_WINDOW = 300        # seconds to remember failures
_FAIL_MAX = 8             # failures allowed per window before lockout
_LOCKOUT = 300            # seconds locked out after exceeding the limit
_failures: dict[str, list[float]] = defaultdict(list)
_locked_until: dict[str, float] = {}


def client_ip(request: Request) -> str:
    """Real client IP. Behind Cloudflare, uvicorn sees 127.0.0.1, so prefer the
    forwarded headers Cloudflare/our proxy sets."""
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_locked(ip: str) -> None:
    now = time.time()
    until = _locked_until.get(ip, 0)
    if until > now:
        raise HTTPException(
            status_code=429,
            detail=f"too many failed logins; try again in {int(until - now)}s",
        )


def _record_failure(ip: str) -> None:
    now = time.time()
    recent = [t for t in _failures[ip] if now - t < _FAIL_WINDOW]
    recent.append(now)
    _failures[ip] = recent
    if len(recent) >= _FAIL_MAX:
        _locked_until[ip] = now + _LOCKOUT
        _failures[ip] = []


@router.post("/login")
def login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = client_ip(request)
    _check_locked(ip)

    user = db.query(User).filter(User.username == username).first()
    if user is None or not verify_password(password, user.password_hash):
        _record_failure(ip)
        raise HTTPException(status_code=401, detail="invalid username or password")

    if user.is_expired:
        raise HTTPException(status_code=403, detail="account access has expired — contact an admin")

    _failures.pop(ip, None)
    _locked_until.pop(ip, None)
    token = make_session_token(user.id)
    response.set_cookie(
        COOKIE_NAME, token, max_age=MAX_AGE, httponly=True, samesite="lax",
        domain=config.COOKIE_DOMAIN,
    )
    return {"ok": True, "username": user.username, "role": user.role}


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, domain=config.COOKIE_DOMAIN)
    return {"ok": True}
