"""User-facing VM routes: list, start/stop, WebRTC, input, IPA install, debug suite."""
import asyncio
import base64
import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path

from fastapi import (
    APIRouter, Depends, File, HTTPException, Request, UploadFile, WebSocket, WebSocketDisconnect,
)
from fastapi.responses import Response
from sqlalchemy.orm import Session

from . import vm_control, vm_manager, webrtc
from .auth import COOKIE_NAME, _read_session, get_current_user
from .db import SessionLocal, get_db
from .models import User, VM

log = logging.getLogger("vphone.routes")
router = APIRouter(prefix="/api", tags=["vm"])


def _visible_vm(db: Session, user: User, vm_id: int) -> VM:
    vm = db.get(VM, vm_id)
    if vm is None:
        raise HTTPException(status_code=404, detail="VM not found")
    if not user.is_admin and user not in vm.users:
        raise HTTPException(status_code=403, detail="not assigned to this VM")
    return vm


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return {
        "id": user.id, "username": user.username, "role": user.role,
        "can_start": user.may_start, "can_stop": user.may_stop,
        "expires_at": user.expires_at.isoformat() if user.expires_at else None,
    }


@router.get("/vms")
def list_vms(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vms = db.query(VM).all() if user.is_admin else list(user.vms)
    out = []
    for vm in vms:
        vm_manager.refresh_status(db, vm)
        out.append(vm.to_dict(include_users=user.is_admin))
    return {"vms": out}


@router.post("/vms/{vm_id}/start")
def start(vm_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    if not user.may_start:
        raise HTTPException(status_code=403, detail="you don't have permission to start VMs")
    try:
        vm_manager.start_vm(db, vm)
    except vm_manager.VMError as e:
        raise HTTPException(status_code=409, detail=str(e))
    return vm.to_dict()


@router.post("/vms/{vm_id}/stop")
def stop(vm_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    if not user.may_stop:
        raise HTTPException(status_code=403, detail="you don't have permission to stop VMs")
    vm_manager.stop_vm(db, vm)
    return vm.to_dict()


@router.post("/vms/{vm_id}/webrtc/offer")
async def webrtc_offer(
    vm_id: int, request: Request,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        raise HTTPException(status_code=409, detail="VM is not ready (starting or stopped)")
    body = await request.json()
    try:
        answer = await webrtc.create_offer_answer(
            vm_manager.socket_path(vm), body["sdp"], body["type"]
        )
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return answer


@router.post("/vms/{vm_id}/key")
async def press_key(
    vm_id: int, request: Request,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    vm = _visible_vm(db, user, vm_id)
    body = await request.json()
    resp = await vm_control.send_command(
        vm_manager.socket_path(vm), {"t": "key", "name": body.get("name", "")}
    )
    return resp


@router.post("/vms/{vm_id}/install")
async def install_ipa(
    vm_id: int, file: UploadFile = File(...),
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        raise HTTPException(status_code=409, detail="VM is not ready (starting or stopped)")
    name = Path(file.filename or "app.ipa").name
    if not name.lower().endswith((".ipa", ".tipa")):
        raise HTTPException(status_code=400, detail="only .ipa or .tipa files are supported")

    tmpdir = Path(tempfile.mkdtemp(prefix="vphone-ipa-"))
    tmp = tmpdir / name
    try:
        with open(tmp, "wb") as f:
            while chunk := await file.read(1 << 20):
                f.write(chunk)
        resp = await vm_control.send_command(
            vm_manager.socket_path(vm), {"t": "install", "path": str(tmp)}, timeout=300
        )
        return resp
    finally:
        try:
            tmp.unlink(missing_ok=True)
            tmpdir.rmdir()
        except OSError:
            pass


# --- chunked upload (bypasses proxy body-size limits, e.g. Cloudflare's 100MB) ---

_UPLOAD_DIR = Path(tempfile.gettempdir()) / "vphone-uploads"


def _part_path(vm_id: int, upload_id: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", upload_id)[:64] or "x"
    return _UPLOAD_DIR / f"{vm_id}-{safe}.part"


@router.post("/vms/{vm_id}/install_chunk")
async def install_chunk(
    vm_id: int, request: Request,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    vm = _visible_vm(db, user, vm_id)
    try:
        upload_id = request.query_params["upload_id"]
        offset = int(request.query_params["offset"])
    except (KeyError, ValueError):
        raise HTTPException(status_code=400, detail="upload_id and offset required")
    if offset < 0:
        raise HTTPException(status_code=400, detail="bad offset")

    _UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    path = _part_path(vm_id, upload_id)
    body = await request.body()
    # Cap total assembled size to a sane ceiling (2 GB) to avoid disk abuse.
    if offset + len(body) > 2 * 1024 * 1024 * 1024:
        path.unlink(missing_ok=True)
        raise HTTPException(status_code=413, detail="file too large")
    flag = "r+b" if path.exists() else "wb"
    with open(path, flag) as f:
        f.seek(offset)
        f.write(body)
    return {"ok": True, "received": len(body)}


@router.post("/vms/{vm_id}/install_finish")
async def install_finish(
    vm_id: int, request: Request,
    user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        raise HTTPException(status_code=409, detail="VM is not ready (starting or stopped)")
    body = await request.json()
    upload_id = body.get("upload_id", "")
    filename = Path(body.get("filename", "app.ipa")).name
    method = "trollstore" if body.get("method") == "trollstore" else "builtin"
    part = _part_path(vm_id, upload_id)
    if not part.exists():
        raise HTTPException(status_code=400, detail="no uploaded data (chunks missing)")
    if not filename.lower().endswith((".ipa", ".tipa")):
        part.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="only .ipa or .tipa files are supported")

    # Rename to the real filename so the installer sees a proper package name.
    final = part.with_name(f"{vm_id}-{re.sub(r'[^A-Za-z0-9_.-]', '', filename)}")
    part.rename(final)
    try:
        resp = await vm_control.send_command(
            vm_manager.socket_path(vm),
            {"t": "install", "path": str(final), "method": method},
            timeout=300,
        )
        return resp
    finally:
        final.unlink(missing_ok=True)


def _ws_user(ws: WebSocket) -> int | None:
    return _read_session(ws.cookies.get(COOKIE_NAME))


@router.websocket("/vms/{vm_id}/mjpeg")
async def mjpeg_ws(ws: WebSocket, vm_id: int):
    """JPEG-frame video over WebSocket. Works through HTTP proxies / Cloudflare
    Tunnel where WebRTC media (UDP/SRTP) cannot reach the host."""
    await ws.accept()
    uid = _ws_user(ws)
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
        if not vm_manager.is_ready(vm):
            await ws.close(code=4409)
            return
        sock = vm_manager.socket_path(vm)
    finally:
        db.close()

    from . import config
    stream = vm_control.FrameStream(
        sock, fps=config.STREAM_FPS, scale=config.STREAM_SCALE, quality=config.STREAM_QUALITY
    )
    try:
        await stream.open()
        while True:
            frame = await stream.read_frame()
            await ws.send_bytes(frame)
    except (WebSocketDisconnect, asyncio.IncompleteReadError, OSError):
        pass
    except Exception:
        log.exception("mjpeg ws error")
    finally:
        await stream.close()


@router.websocket("/vms/{vm_id}/input")
async def input_ws(ws: WebSocket, vm_id: int):
    await ws.accept()
    uid = _ws_user(ws)
    if uid is None:
        await ws.close(code=4401)
        return

    # Resolve VM + permission with a short-lived session.
    db = SessionLocal()
    try:
        user = db.get(User, uid)
        vm = db.get(VM, vm_id)
        if user is None or vm is None or (not user.is_admin and user not in vm.users):
            await ws.close(code=4403)
            return
        if not vm_manager.is_ready(vm):
            await ws.close(code=4409)
            return
        sock = vm_manager.socket_path(vm)
    finally:
        db.close()

    channel = vm_control.CommandChannel(sock)
    try:
        await channel.open()
    except OSError:
        await ws.close(code=4503)
        return

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = msg.get("t")
            if t == "touch":
                cmd = {"t": "touch", "phase": int(msg["phase"]),
                       "nx": float(msg["nx"]), "ny": float(msg["ny"])}
                await channel.send(cmd, read_reply=False)
            elif t == "key":
                await channel.send({"t": "key", "name": msg.get("name", "")}, read_reply=False)
            elif t == "type":
                await channel.send({"t": "type", "text": msg.get("text", "")}, read_reply=False)
            elif t == "text":  # real keystrokes from the physical keyboard
                await channel.send({"t": "text", "text": msg.get("text", "")}, read_reply=False)
            elif t == "speckey":  # Enter / Backspace / arrows / etc.
                await channel.send({"t": "speckey", "name": msg.get("name", "")}, read_reply=False)
            elif t:  # raw JSON command passthrough (Console tab) — e.g. {"t":"ping"}
                await channel.send(msg, read_reply=False)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("input ws error")
    finally:
        await channel.close()


@router.websocket("/vms/{vm_id}/pty")
async def pty_ws(ws: WebSocket, vm_id: int):
    """Interactive root PTY (xterm.js front-end). Bridges raw bytes:
    browser <-WS-> here <-unix socket-> Swift relay <-vsock 1339-> vphoned forkpty."""
    await ws.accept()
    uid = _ws_user(ws)
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
        if not vm_manager.is_ready(vm):
            await ws.close(code=4409)
            return
        sock = vm_manager.socket_path(vm)
    finally:
        db.close()

    # First WS message carries the initial terminal size.
    try:
        cfg = json.loads(await ws.receive_text())
        cols = max(1, min(500, int(cfg.get("cols", 80))))
        rows = max(1, min(300, int(cfg.get("rows", 24))))
    except (WebSocketDisconnect, json.JSONDecodeError, ValueError, TypeError):
        await ws.close()
        return

    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_unix_connection(sock, limit=vm_control.READ_LIMIT), timeout=5
        )
    except (OSError, asyncio.TimeoutError):
        await ws.close(code=4503)
        return

    # Flip the Swift stream-server connection into PTY relay mode.
    writer.write((json.dumps({"t": "pty", "cols": cols, "rows": rows}) + "\n").encode())
    await writer.drain()

    async def guest_to_ws():
        while True:
            data = await reader.read(16384)
            if not data:
                break
            await ws.send_bytes(data)

    async def ws_to_guest():
        while True:
            msg = await ws.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            b = msg.get("bytes")
            if b is not None:
                # Raw keystrokes / paste from xterm.
                writer.write(b)
                await writer.drain()
                continue
            # Text frames are control messages (e.g. {"resize":[c,r]}). Live
            # resize isn't supported over the raw channel, so drop them rather
            # than inject JSON into the PTY's stdin.

    g1 = asyncio.create_task(guest_to_ws())
    g2 = asyncio.create_task(ws_to_guest())
    try:
        await asyncio.wait({g1, g2}, return_when=asyncio.FIRST_COMPLETED)
    except Exception:
        pass
    finally:
        g1.cancel()
        g2.cancel()
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass
        try:
            await ws.close()
        except Exception:
            pass


# ════════════════════════════════════════════════════════════════════
#  Debug suite (Apps / Crashes / Metrics / Clipboard / Diagnostics)
#  These proxy to vphoned via the per-VM stream socket. Advanced features
#  (auto-debug / lldb / per-app logs) degrade gracefully.
# ════════════════════════════════════════════════════════════════════

CRASH_DIR = "/var/mobile/Library/Logs/CrashReporter"


async def _vm_cmd(vm: VM, command: dict, timeout: float = 30.0) -> dict:
    return await vm_control.send_command(vm_manager.socket_path(vm), command, timeout=timeout)


@router.get("/vms/{vm_id}/metrics")
def vm_metrics(vm_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    ready = vm_manager.is_ready(vm)
    proc = vm_manager._PROCS.get(vm.id)
    pid = (proc.pid if proc and proc.poll() is None else (vm.pid or 0))
    rss_mb = cpu_pct = uptime = 0.0
    if pid:
        try:
            out = subprocess.run(
                ["ps", "-o", "rss=,%cpu=,etime=", "-p", str(pid)],
                capture_output=True, text=True, timeout=3,
            ).stdout.split()
            if len(out) >= 3:
                rss_mb = round(int(out[0]) / 1024, 1)
                cpu_pct = float(out[1])
                et = out[2]  # [[dd-]hh:]mm:ss
                parts = et.replace("-", ":").split(":")
                nums = [int(p) for p in parts]
                while len(nums) < 3:
                    nums.insert(0, 0)
                d = nums[-4] if len(nums) == 4 else 0
                uptime = d * 86400 + nums[-3] * 3600 + nums[-2] * 60 + nums[-1]
        except (ValueError, subprocess.SubprocessError, IndexError):
            pass
    return {
        "fps": {"current": 0, "avg": 0, "p95": 0},
        "latency": {"avg_ms": 0, "p95_ms": 0, "max_ms": 0},
        "video": {"frames_received": 0, "frames_dropped": 0, "connected": ready, "uptime_sec": uptime},
        "process": {"rss_mb": rss_mb, "pid": pid, "uptime_sec": uptime, "cpu_pct": cpu_pct},
        "input": {"connected": ready, "commands_ok": 0, "commands_fail": 0, "uptime_sec": uptime, "reconnects": 0},
        "stream": {"bytes_sent": 0},
    }


@router.get("/vms/{vm_id}/clipboard")
async def vm_clipboard(vm_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        return {"ok": False, "error": "VM not ready"}
    r = await _vm_cmd(vm, {"t": "clipboard_get"})
    if r.get("ok"):
        text = (r.get("data") or {}).get("text", "") or ""
        return {"ok": True, "text": text, "empty": not text}
    return {"ok": False, "error": r.get("error", "clipboard read failed")}


# ---- Filesystem browser (Filza-style; vphoned runs as root → full / access) ----

@router.get("/vms/{vm_id}/fs")
async def vm_fs_list(vm_id: int, path: str = "/", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """List a directory in the guest. vphoned runs as root so any path from / works."""
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        return {"ok": False, "error": "VM not ready"}
    if not path.startswith("/"):
        path = "/" + path
    r = await _vm_cmd(vm, {"t": "file_list", "path": path}, timeout=60)
    if r.get("ok"):
        entries = r.get("data") or []
        return {"ok": True, "path": path, "entries": entries}
    return {"ok": False, "error": r.get("error") or r.get("msg") or "list failed"}


@router.post("/vms/{vm_id}/fs/upload")
async def vm_fs_upload(vm_id: int, path: str = "/", file: UploadFile = File(...),
                       user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Upload a file into a guest directory (base64 over vsock via file_put)."""
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        return {"ok": False, "error": "VM not ready"}
    data = await file.read()
    # Base64 rides in the JSON command line; cap to keep memory + proxy limits sane.
    if len(data) > 64 * 1024 * 1024:
        return {"ok": False, "error": "file too large (max 64 MB via this uploader)"}
    name = (file.filename or "upload").rsplit("/", 1)[-1]
    dest = (path.rstrip("/") or "") + "/" + name
    b64 = base64.b64encode(data).decode()
    r = await _vm_cmd(vm, {"t": "file_put", "path": dest, "data": b64, "perm": "644"}, timeout=300)
    if r.get("ok"):
        return {"ok": True, "path": dest, "size": len(data)}
    return {"ok": False, "error": r.get("error") or r.get("msg") or "upload failed"}


@router.get("/vms/{vm_id}/fs/download")
async def vm_fs_download(vm_id: int, path: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Download a single file from the guest (base64 over vsock, decoded here)."""
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        raise HTTPException(status_code=409, detail="VM not ready")
    r = await _vm_cmd(vm, {"t": "file_get", "path": path}, timeout=300)
    if not r.get("ok"):
        raise HTTPException(status_code=404, detail=r.get("error") or r.get("msg") or "read failed")
    try:
        blob = base64.b64decode(r.get("data") or "")
    except (ValueError, TypeError):
        raise HTTPException(status_code=500, detail="bad file payload")
    name = path.rstrip("/").rsplit("/", 1)[-1] or "file"
    return Response(
        content=blob, media_type="application/octet-stream",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/vms/{vm_id}/apps")
async def vm_apps(vm_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        raise HTTPException(status_code=409, detail="VM not ready")
    r = await _vm_cmd(vm, {"t": "app_list"}, timeout=60)
    if r.get("ok"):
        return {"ok": True, "apps": r.get("data") or []}
    return {"ok": False, "apps": [], "error": r.get("error", "app list failed")}


@router.post("/vms/{vm_id}/apps/{bundle_id}/launch")
async def vm_app_launch(vm_id: int, bundle_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    r = await _vm_cmd(vm, {"t": "app_launch", "bundle_id": bundle_id})
    return {"ok": bool(r.get("ok")), "pid": r.get("data"), "error": r.get("error")}


@router.post("/vms/{vm_id}/apps/{bundle_id}/foreground")
async def vm_app_foreground(vm_id: int, bundle_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    # Bringing an app to the foreground = (re)launching it.
    r = await _vm_cmd(vm, {"t": "app_launch", "bundle_id": bundle_id})
    return {"ok": bool(r.get("ok")), "error": r.get("error")}


@router.post("/vms/{vm_id}/apps/{bundle_id}/kill")
async def vm_app_kill(vm_id: int, bundle_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    r = await _vm_cmd(vm, {"t": "app_terminate", "bundle_id": bundle_id})
    return {"ok": bool(r.get("ok")), "error": r.get("error")}


@router.get("/vms/{vm_id}/apps/{bundle_id}/debug-logs")
def vm_app_debug_logs(vm_id: int, bundle_id: str, lines: int = 200, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _visible_vm(db, user, vm_id)
    # Per-app os_log capture is not wired up; return empty buckets (UI tolerates).
    return {"app_logs": [], "kernel_logs": [], "system_logs": [], "crash_logs": []}


@router.post("/vms/{vm_id}/apps/{bundle_id}/debug")
def vm_app_debug(vm_id: int, bundle_id: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _visible_vm(db, user, vm_id)
    return {"ok": False, "error": "auto-debug (debugserver/lldb) is not available in this build"}


@router.get("/vms/{vm_id}/crashes")
async def vm_crashes(vm_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        return {"crashes": []}
    r = await _vm_cmd(vm, {"t": "file_list", "path": CRASH_DIR}, timeout=60)
    crashes = []
    if r.get("ok"):
        for e in (r.get("data") or []):
            name = e.get("name", "")
            low = name.lower()
            if low.endswith((".ips", ".panic", ".crash")) or "crash" in low:
                crashes.append({"name": name, "dir": CRASH_DIR})
    return {"crashes": crashes}


@router.get("/vms/{vm_id}/crashes/{crash_path:path}")
async def vm_crash_content(vm_id: int, crash_path: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        return {"ok": False, "error": "VM not ready"}
    # Robust to URL-encoding quirks: always resolve within the crash dir by name.
    name = crash_path.rstrip("/").split("/")[-1]
    full = f"{CRASH_DIR}/{name}"
    r = await _vm_cmd(vm, {"t": "file_get", "path": full}, timeout=120)
    if r.get("ok"):
        try:
            text = base64.b64decode(r["data"]).decode("utf-8", errors="replace")
        except Exception:
            text = ""
        return {"ok": True, "data": text}
    return {"ok": False, "error": r.get("error", "read failed")}


@router.get("/vms/{vm_id}/diagnostics")
def vm_diagnostics(vm_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vm = _visible_vm(db, user, vm_id)
    boot = []
    p = vm_manager.vm_dir(vm) / "boot.log"
    if p.exists():
        try:
            boot = p.read_text(errors="replace").splitlines()[-200:]
        except OSError:
            pass
    return {"logs_tail": {"boot": boot}}


@router.post("/vms/{vm_id}/shell")
async def vm_shell(vm_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Run one shell command inside the guest (root) and return its output.
    `cwd` is round-tripped so the web terminal can keep `cd` state."""
    vm = _visible_vm(db, user, vm_id)
    if not vm_manager.is_ready(vm):
        return {"ok": False, "error": "VM not ready"}
    body = await request.json()
    cmd = (body.get("cmd") or "").strip()
    cwd = body.get("cwd") or "/var/root"
    if not cmd:
        return {"ok": False, "error": "empty command"}
    r = await _vm_cmd(vm, {"t": "shell_exec", "cmd": cmd, "cwd": cwd}, timeout=120)
    if r.get("ok"):
        d = r.get("data") or {}
        return {
            "ok": True,
            "output": d.get("output", ""),
            "exit": d.get("exit", -1),
            "cwd": d.get("cwd", cwd),
            "shell": d.get("shell", ""),
        }
    return {"ok": False, "error": r.get("error") or r.get("msg") or "shell failed"}


@router.websocket("/vms/{vm_id}/lldb-terminal")
async def vm_lldb_terminal(ws: WebSocket, vm_id: int):
    await ws.accept()
    if _ws_user(ws) is None:
        await ws.close(code=4401)
        return
    try:
        await ws.send_text("LLDB terminal is not available in this build.\r\n")
        while True:
            await ws.receive_text()  # accept and ignore input
    except (WebSocketDisconnect, Exception):
        pass
