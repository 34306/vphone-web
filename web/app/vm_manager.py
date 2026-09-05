"""VM lifecycle: clone the golden image, start/stop headless processes."""
import os
import plistlib
import shutil
import signal
import subprocess
import time
import uuid
from pathlib import Path


class _AdoptedProc:
    """Lightweight stand-in for a VM process started by a previous web-server
    run (re-adopted on startup so server restarts don't orphan VMs)."""

    def __init__(self, pid: int):
        self.pid = pid

    def poll(self):
        try:
            os.kill(self.pid, 0)
            return None
        except OSError:
            return 0

    def send_signal(self, sig):
        try:
            os.kill(self.pid, sig)
        except OSError:
            pass

    def wait(self, timeout=None):
        deadline = None if timeout is None else time.time() + timeout
        while self.poll() is None:
            if deadline is not None and time.time() > deadline:
                raise subprocess.TimeoutExpired(self.pid, timeout)
            time.sleep(0.2)
        return 0

    def kill(self):
        self.send_signal(signal.SIGKILL)

from sqlalchemy.orm import Session

from . import config
from .models import VM

# vm_id -> Popen of the running vphone-cli process
_PROCS: dict[int, subprocess.Popen] = {}

# How long to wait for a clean iOS shutdown (SIGINT) before force-killing.
# Must be generous: on slow storage the guest's APFS unmount/flush can take
# a while, and force-killing early corrupts the data volume (dirty unmount ->
# slow fsck replay + possible boot wedge on next start). Override via env.
# Slightly longer than the app's own internal graceful-stop fallback (120s) so
# the guest's clean power-off completes before we resort to SIGKILL.
STOP_GRACE_SECONDS = int(os.environ.get("VPHONE_STOP_GRACE_SECONDS", "150"))


class VMError(Exception):
    pass


def vm_dir(vm: VM) -> Path:
    return config.VMS_DIR / vm.dir_name


def socket_path(vm: VM) -> str:
    return str(vm_dir(vm) / "vphone.sock")


def is_running(vm: VM) -> bool:
    proc = _PROCS.get(vm.id)
    return proc is not None and proc.poll() is None


def is_ready(vm: VM) -> bool:
    """Process alive AND control socket present — ready to serve video/input."""
    return is_running(vm) and Path(socket_path(vm)).exists()


# ---------------------------------------------------------------- create

def create_vm(db: Session, name: str, cpu: int, mem_mb: int, ios_version: str = "26.1") -> VM:
    if db.query(VM).filter(VM.name == name).first():
        raise VMError(f"a VM named {name!r} already exists")
    base = config.base_for_version(ios_version)
    if not (base / "Disk.img").exists():
        raise VMError(f"iOS {ios_version} base image not found at {base}")

    dir_name = uuid.uuid4().hex[:12]
    dest = config.VMS_DIR / dir_name
    dest.mkdir(parents=True, exist_ok=False)

    try:
        # CoW clone the big files (instant on APFS), copy the rest — from the
        # base image matching the requested iOS version.
        for fname in config.VM_CLONE_FILES:
            src = base / fname
            if src.exists():
                subprocess.run(["cp", "-c", str(src), str(dest / fname)], check=True)
        for fname in config.VM_COPY_FILES:
            src = base / fname
            if src.exists():
                shutil.copy2(src, dest / fname)

        _apply_resources(dest / "config.plist", cpu, mem_mb)
    except Exception:
        shutil.rmtree(dest, ignore_errors=True)
        raise

    vm = VM(name=name, dir_name=dir_name, cpu=cpu, mem_mb=mem_mb,
            ios_version=ios_version, status="stopped")
    db.add(vm)
    db.commit()
    db.refresh(vm)
    return vm


def _apply_resources(plist_path: Path, cpu: int, mem_mb: int) -> None:
    with open(plist_path, "rb") as f:
        data = plistlib.load(f)
    data["cpuCount"] = cpu
    data["memorySize"] = mem_mb * 1024 * 1024
    with open(plist_path, "wb") as f:
        plistlib.dump(data, f)


# ---------------------------------------------------------------- start/stop

def running_memory_mb(db: Session) -> int:
    total = 0
    for vm in db.query(VM).all():
        if is_running(vm):
            total += vm.mem_mb
    return total


def start_vm(db: Session, vm: VM) -> None:
    if is_running(vm):
        return
    used = running_memory_mb(db)
    if used + vm.mem_mb > config.RAM_BUDGET_MB:
        raise VMError(
            f"RAM budget exceeded: {used}MB running + {vm.mem_mb}MB requested "
            f"> {config.RAM_BUDGET_MB}MB. Stop another VM first."
        )

    d = vm_dir(vm)
    config_plist = d / "config.plist"
    if not config_plist.exists():
        raise VMError(f"config.plist missing for VM {vm.name}")
    if not config.VPHONE_BIN.exists():
        raise VMError(f"vphone-cli binary not found at {config.VPHONE_BIN} (run `make build`)")

    sock = d / "vphone.sock"
    if sock.exists():
        sock.unlink()

    log = open(d / "boot.log", "wb")
    cmd = [
        str(config.VPHONE_BIN), "boot",
        "--config", str(config_plist),
        "--server",
        "--control-socket", str(sock),
        "--variant", config.VM_VARIANT,
        "--vphoned-bin", str(d / ".vphoned.signed"),
    ]
    # cwd = repo root so the binary can locate scripts/vphoned/signcert.p12 for IPA signing.
    proc = subprocess.Popen(cmd, cwd=str(config.REPO_ROOT), stdout=log, stderr=subprocess.STDOUT)
    _PROCS[vm.id] = proc

    # Only catch an *immediate* failure (bad config/binary). The control socket
    # can take a long time to appear on slow disks, so we do NOT block-wait or
    # kill here — the VM comes up as "starting" and flips to "running" once its
    # socket exists (see refresh_status). This avoids orphaning slow-booting VMs.
    deadline = time.time() + 8
    while time.time() < deadline:
        if proc.poll() is not None:
            raise VMError(f"vphone-cli exited early (code {proc.returncode}); see {d/'boot.log'}")
        if sock.exists():
            break
        time.sleep(0.3)

    vm.status = "running" if sock.exists() else "starting"
    vm.pid = proc.pid
    db.commit()


def stop_vm(db: Session, vm: VM) -> None:
    proc = _PROCS.pop(vm.id, None)
    if proc is not None and proc.poll() is None:
        proc.send_signal(signal.SIGINT)  # AppDelegate handles SIGINT -> clean terminate
        # iOS needs to flush/unmount the data volume cleanly. On slow storage
        # (e.g. USB) this takes well over 15s; force-killing early leaves the
        # APFS volume dirty -> the next boot does a slow fsck replay and can
        # wedge before SpringBoard. Give it a generous window before SIGKILL.
        try:
            proc.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            print(f"[vm_manager] {vm.name}: clean shutdown exceeded {STOP_GRACE_SECONDS}s, forcing kill (disk may be dirty)")
            proc.kill()
    sock = vm_dir(vm) / "vphone.sock"
    if sock.exists():
        sock.unlink()
    vm.status = "stopped"
    vm.pid = None
    db.commit()


def delete_vm(db: Session, vm: VM) -> None:
    stop_vm(db, vm)
    shutil.rmtree(vm_dir(vm), ignore_errors=True)
    db.delete(vm)
    db.commit()


# ---------------------------------------------------------------- reconcile

def _find_pid_for_socket(sock: str) -> int | None:
    """Find a running vphone-cli process whose --control-socket is `sock`."""
    try:
        out = subprocess.run(["pgrep", "-f", sock], capture_output=True, text=True)
    except OSError:
        return None
    for line in out.stdout.split():
        try:
            return int(line)
        except ValueError:
            continue
    return None


def reconcile_on_startup(db: Session) -> None:
    """Re-adopt VM processes that survived a web-server restart; mark the rest
    stopped. This keeps running devices usable across `make web_run` restarts."""
    for vm in db.query(VM).all():
        pid = _find_pid_for_socket(socket_path(vm))
        if pid is not None:
            _PROCS[vm.id] = _AdoptedProc(pid)
            vm.status = "running"
            vm.pid = pid
        else:
            vm.status = "stopped"
            vm.pid = None
    db.commit()


def stop_all() -> None:
    for vm_id, proc in list(_PROCS.items()):
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
    for vm_id, proc in list(_PROCS.items()):
        try:
            proc.wait(timeout=STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
    _PROCS.clear()


def refresh_status(db: Session, vm: VM) -> None:
    """Reconcile DB status with reality: dead process -> stopped; alive with
    socket -> running; alive without socket yet -> starting."""
    if vm.status not in ("running", "starting"):
        return
    if not is_running(vm):
        new = "stopped"
    elif Path(socket_path(vm)).exists():
        new = "running"
    else:
        new = "starting"
    if new != vm.status:
        vm.status = new
        vm.pid = None if new == "stopped" else vm.pid
        db.commit()
