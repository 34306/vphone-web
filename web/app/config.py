"""Central configuration for the vphone web platform."""
import os
import secrets
from pathlib import Path

# Repo layout: this file is web/app/config.py -> repo root is parents[2]
REPO_ROOT = Path(__file__).resolve().parents[2]

# Golden image cloned for every new VM (default / iOS 26.1 base).
BASE_VM_DIR = Path(os.environ.get("VPHONE_BASE_VM", REPO_ROOT / "vm"))

# Per-iOS-version base images. Each new VM is a copy-on-write clone of the base
# for the chosen version. 26.1 = the original `vm/` base; 26.5 = `vm-26.5/`;
# 27 = `vm-27/` (iOS 27.0 24A5390f + cloudOS 26.4, jailbreak variant).
BASE_IMAGES = {
    "26.1": BASE_VM_DIR,
    "26.5": Path(os.environ.get("VPHONE_BASE_VM_265", REPO_ROOT / "vm-26.5")),
    "27": Path(os.environ.get("VPHONE_BASE_VM_27", REPO_ROOT / "vm-27")),
}
DEFAULT_IOS_VERSION = os.environ.get("VPHONE_DEFAULT_IOS", "26.1")


def base_for_version(version: str) -> Path:
    """Resolve the base-image directory for an iOS version (falls back to default)."""
    return BASE_IMAGES.get(version, BASE_IMAGES[DEFAULT_IOS_VERSION])


def available_ios_versions() -> list[str]:
    """Versions whose base image actually exists on disk (so the UI only offers usable ones)."""
    return [v for v, p in BASE_IMAGES.items() if (p / "Disk.img").exists()]


# Where per-VM directories live.
VMS_DIR = Path(os.environ.get("VPHONE_VMS_DIR", REPO_ROOT / "vms"))
# Persistent data (sqlite db, secret key).
DATA_DIR = Path(os.environ.get("VPHONE_DATA_DIR", REPO_ROOT / "web" / "data"))

# Signed vphone-cli binary. Prefer the app bundle (bundles signcert.p12 for IPA
# signing); fall back to the raw release binary.
_BUNDLE_BIN = REPO_ROOT / ".build" / "vphone-cli.app" / "Contents" / "MacOS" / "vphone-cli"
_RAW_BIN = REPO_ROOT / ".build" / "release" / "vphone-cli"
VPHONE_BIN = Path(os.environ.get("VPHONE_BIN", _BUNDLE_BIN if _BUNDLE_BIN.exists() else _RAW_BIN))

# Files that make up a VM directory (cloned from BASE_VM_DIR).
# Large disk/nvram files are CoW-cloned via `cp -c`; the rest copied normally.
VM_CLONE_FILES = ["Disk.img", "nvram.bin"]
VM_COPY_FILES = [
    "SEPStorage",
    "AVPBooter.vresearch1.bin",
    "AVPSEPBooter.vresearch1.bin",
    "config.plist",
    ".vphoned.signed",
]

# Resource limits. Host is 16GB; keep headroom for macOS + the web server.
DEFAULT_VM_CPU = int(os.environ.get("VPHONE_DEFAULT_CPU", "4"))
DEFAULT_VM_MEM_MB = int(os.environ.get("VPHONE_DEFAULT_MEM_MB", "4096"))
# Refuse to start a VM if running VMs' total memory would exceed this.
RAM_BUDGET_MB = int(os.environ.get("VPHONE_RAM_BUDGET_MB", "12288"))

# Firmware variant used when booting VMs.
VM_VARIANT = os.environ.get("VPHONE_VARIANT", "regular")

# Streaming defaults (passed to the Swift stream server).
STREAM_FPS = int(os.environ.get("VPHONE_STREAM_FPS", "20"))
STREAM_SCALE = int(os.environ.get("VPHONE_STREAM_SCALE", "2"))
STREAM_QUALITY = float(os.environ.get("VPHONE_STREAM_QUALITY", "0.6"))

# Web server bind.
HOST = os.environ.get("VPHONE_HOST", "127.0.0.1")
PORT = int(os.environ.get("VPHONE_PORT", "8080"))

# Optional parent cookie domain so the session cookie is shared across
# subdomains (e.g. set ".vphone.local" so login on vphone.local also authorizes
# logs.vphone.local). Leave empty for single-host / 127.0.0.1 usage.
COOKIE_DOMAIN = os.environ.get("VPHONE_COOKIE_DOMAIN") or None

# Optional admin auto-seed at startup (only if no admin exists yet).
ADMIN_USER = os.environ.get("VPHONE_ADMIN_USER")
ADMIN_PASS = os.environ.get("VPHONE_ADMIN_PASS")

DB_PATH = DATA_DIR / "app.db"
_SECRET_FILE = DATA_DIR / "secret.key"


def get_secret_key() -> str:
    """Stable signing key for session cookies (persisted across restarts)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_text().strip()
    key = secrets.token_hex(32)
    _SECRET_FILE.write_text(key)
    _SECRET_FILE.chmod(0o600)
    return key
