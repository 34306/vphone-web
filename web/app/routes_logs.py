"""Logs dashboard: view / live-tail boot.log (admin, or the VM's assigned user)."""
import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from . import vm_manager
from .auth import COOKIE_NAME, _read_session, require_admin
from .db import SessionLocal, get_db
from .models import User, VM

log = logging.getLogger("vphone.logs")
router = APIRouter(prefix="/api/logs", tags=["logs"])

MAX_BYTES = 256 * 1024  # cap how much of a log we read for a tail


def _log_path(vm: VM):
    return vm_manager.vm_dir(vm) / "boot.log"


def _tail(vm: VM, lines: int) -> str:
    path = _log_path(vm)
    if not path.exists():
        return ""
    size = path.stat().st_size
    with open(path, "rb") as f:
        if size > MAX_BYTES:
            f.seek(size - MAX_BYTES)
            f.readline()  # drop partial first line
        data = f.read()
    text = data.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


@router.get("")
def list_logs(_: User = Depends(require_admin), db: Session = Depends(get_db)):
    out = []
    for vm in db.query(VM).all():
        vm_manager.refresh_status(db, vm)
        path = _log_path(vm)
        out.append({
            "id": vm.id,
            "name": vm.name,
            "status": vm.status,
            "has_log": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
        })
    return {"vms": out}


@router.get("/{vm_id}")
def get_log(vm_id: int, lines: int = 400, _: User = Depends(require_admin), db: Session = Depends(get_db)):
    vm = db.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail="VM not found")
    return {"id": vm.id, "name": vm.name, "text": _tail(vm, min(lines, 5000))}


def _ws_admin(ws: WebSocket) -> bool:
    uid = _read_session(ws.cookies.get(COOKIE_NAME))
    if uid is None:
        return False
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        return user is not None and user.is_admin
    finally:
        db.close()


@router.websocket("/{vm_id}/stream")
async def stream_log(ws: WebSocket, vm_id: int):
    await ws.accept()
    # Auth: admin, or a user this VM is assigned to (so the per-device Logs tab
    # works for the assigned operator too).
    uid = _read_session(ws.cookies.get(COOKIE_NAME))
    if uid is None:
        await ws.close(code=4401)
        return
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        vm = db.get(VM, vm_id)
        if user is None or vm is None or (not user.is_admin and user not in vm.users):
            await ws.close(code=4403)
            return
        path = _log_path(vm)
    finally:
        db.close()

    # JSON framing ({type, data}) when requested by the embedded DebugLogViewer.
    as_json = ws.query_params.get("format") == "json"

    async def emit(text: str, kind: str = "chunk"):
        if as_json:
            await ws.send_text(json.dumps({"type": kind, "data": text}))
        else:
            await ws.send_text(text if kind == "chunk" else f"\n--- {text} ---\n")

    try:
        if path.exists():
            with open(path, "rb") as f:
                size = f.seek(0, 2)
                start = max(0, size - MAX_BYTES)
                f.seek(start)
                if start:
                    f.readline()
                await emit(f.read().decode("utf-8", errors="replace"))
                pos = f.tell()
        else:
            pos = 0
            await emit("(waiting for log to appear…)\n")

        while True:
            await asyncio.sleep(0.5)
            if not path.exists():
                continue
            cur = path.stat().st_size
            if cur < pos:  # log rotated/truncated (VM restarted)
                pos = 0
                await emit("log reset (VM restarted)", "reset")
            if cur > pos:
                with open(path, "rb") as f:
                    f.seek(pos)
                    chunk = f.read()
                    pos = f.tell()
                await emit(chunk.decode("utf-8", errors="replace"))
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("log stream error")
