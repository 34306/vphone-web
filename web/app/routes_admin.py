"""Admin routes: manage users, VMs, and assignments."""
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from . import config, vm_manager
from .auth import hash_password, require_admin
from .db import get_db
from .models import User, VM

router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(require_admin)])

# Primary admin account that can never be demoted or deleted (lockout guard).
# Set VPHONE_PROTECTED_ADMIN=<username> to protect one account; empty = none.
PROTECTED_ADMIN = os.environ.get("VPHONE_PROTECTED_ADMIN", "")

# Access-duration presets (key -> days). "unlimited"/None => no expiry.
DURATIONS = {"1d": 1, "1w": 7, "1mo": 30, "3mo": 90, "6mo": 180, "1y": 365}


def _resolve_expiry(duration):
    """Map a duration key to an absolute UTC expiry, or None for unlimited."""
    if not duration or duration == "unlimited":
        return None
    days = DURATIONS.get(str(duration))
    if days is None:
        raise HTTPException(status_code=400, detail=f"invalid duration: {duration}")
    return datetime.now(timezone.utc) + timedelta(days=days)


def _user_dict(u: User) -> dict:
    return {
        "id": u.id, "username": u.username, "role": u.role,
        "vm_ids": [vm.id for vm in u.vms],
        "expires_at": u.expires_at.isoformat() if u.expires_at else None,
        "can_start": bool(u.can_start), "can_stop": bool(u.can_stop),
        "protected": bool(PROTECTED_ADMIN) and u.username == PROTECTED_ADMIN,
    }


# ---------------------------------------------------------------- users

@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    users = db.query(User).all()
    return {"users": [_user_dict(u) for u in users]}


@router.post("/users")
async def create_user(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = body.get("role", "user")
    if not username or not password:
        raise HTTPException(status_code=400, detail="username and password required")
    if role not in ("admin", "user"):
        raise HTTPException(status_code=400, detail="role must be admin or user")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="username already exists")
    # Admins are always unlimited; users get the chosen duration (default unlimited).
    expires_at = None if role == "admin" else _resolve_expiry(body.get("duration"))
    user = User(
        username=username, password_hash=hash_password(password), role=role,
        expires_at=expires_at,
        can_start=1 if body.get("can_start") else 0,
        can_stop=1 if body.get("can_stop") else 0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_dict(user)


@router.patch("/users/{user_id}")
async def update_user(user_id: int, request: Request, db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    body = await request.json()
    if "password" in body and body["password"]:
        user.password_hash = hash_password(body["password"])
    if "role" in body:
        if body["role"] not in ("admin", "user"):
            raise HTTPException(status_code=400, detail="invalid role")
        # Protect the primary admin from being demoted (would risk lockout).
        if user.username == PROTECTED_ADMIN and body["role"] != "admin":
            raise HTTPException(status_code=400, detail="cannot change role of the protected admin account")
        user.role = body["role"]
    if "duration" in body:
        user.expires_at = _resolve_expiry(body["duration"])
    if "can_start" in body:
        user.can_start = 1 if body["can_start"] else 0
    if "can_stop" in body:
        user.can_stop = 1 if body["can_stop"] else 0
    # Admins are always unlimited regardless of any duration set.
    if user.is_admin:
        user.expires_at = None
    db.commit()
    return {"ok": True}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    if user.username == PROTECTED_ADMIN:
        raise HTTPException(status_code=400, detail="cannot delete the protected admin account")
    db.delete(user)
    db.commit()
    return {"ok": True}


# ---------------------------------------------------------------- vms

@router.get("/vms")
def list_all_vms(db: Session = Depends(get_db)):
    vms = db.query(VM).all()
    for vm in vms:
        vm_manager.refresh_status(db, vm)
    return {"vms": [vm.to_dict(include_users=True) for vm in vms]}


@router.get("/ios-versions")
def ios_versions():
    """iOS base images available on this host (for the Create VM dropdown)."""
    avail = config.available_ios_versions()
    return {"versions": avail, "default": config.DEFAULT_IOS_VERSION if config.DEFAULT_IOS_VERSION in avail else (avail[0] if avail else None)}


@router.post("/vms")
async def create_vm(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    cpu = int(body.get("cpu", config.DEFAULT_VM_CPU))
    mem_mb = int(body.get("mem_mb", config.DEFAULT_VM_MEM_MB))
    ios_version = str(body.get("ios_version") or config.DEFAULT_IOS_VERSION)
    if ios_version not in config.BASE_IMAGES:
        raise HTTPException(status_code=400, detail=f"unknown iOS version: {ios_version}")
    try:
        vm = vm_manager.create_vm(db, name, cpu, mem_mb, ios_version=ios_version)
    except vm_manager.VMError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return vm.to_dict(include_users=True)


@router.delete("/vms/{vm_id}")
def delete_vm(vm_id: int, db: Session = Depends(get_db)):
    vm = db.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail="VM not found")
    vm_manager.delete_vm(db, vm)
    return {"ok": True}


@router.post("/vms/{vm_id}/assign")
async def assign_vm(vm_id: int, request: Request, db: Session = Depends(get_db)):
    vm = db.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail="VM not found")
    body = await request.json()
    user_ids = body.get("user_ids", [])
    users = db.query(User).filter(User.id.in_(user_ids)).all() if user_ids else []
    vm.users = users
    db.commit()
    return vm.to_dict(include_users=True)
