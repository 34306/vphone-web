"""Server-wide debug/status endpoint for the System panel."""
import platform
import subprocess
import sys
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from . import config, vm_manager, webrtc
from .auth import get_current_user
from .db import get_db
from .models import VM, User

router = APIRouter(prefix="/api/debug", tags=["debug"])

_START = time.time()


def _dir_size_mb(path) -> float:
    try:
        out = subprocess.run(["du", "-sk", str(path)], capture_output=True, text=True, timeout=10).stdout
        return round(int(out.split()[0]) / 1024, 1)
    except (subprocess.SubprocessError, ValueError, IndexError):
        return 0.0


@router.get("/server")
def server(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    vms = db.query(VM).all()
    running = sum(1 for v in vms if vm_manager.is_running(v))
    try:
        cpu_count = subprocess.run(["sysctl", "-n", "hw.ncpu"], capture_output=True, text=True, timeout=3).stdout.strip()
        cpu_count = int(cpu_count or 0)
    except Exception:
        cpu_count = 0
    return {
        "host": {
            "hostname": platform.node(),
            "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
            "cpu_count": cpu_count,
        },
        "server": {
            "uptime_sec": round(time.time() - _START),
            "python_version": platform.python_version(),
        },
        "vms": {"running": running, "total": len(vms)},
        "ram": {"used_mb": vm_manager.running_memory_mb(db), "budget_mb": config.RAM_BUDGET_MB},
        "storage": {"vms_dir_size_mb": _dir_size_mb(config.VMS_DIR)},
        "webrtc": {"active_connections": len(getattr(webrtc, "_PCS", []))},
    }
