"""FastAPI entrypoint for the vphone web platform."""
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, vm_manager, webrtc
from .db import SessionLocal, init_db
from .models import User
from .routes_admin import router as admin_router
from .routes_auth import router as auth_router
from .routes_debug import router as debug_router
from .routes_logs import router as logs_router
from .routes_vm import router as vm_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("vphone")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _maybe_seed_admin() -> None:
    if not (config.ADMIN_USER and config.ADMIN_PASS):
        return
    db = SessionLocal()
    try:
        has_admin = db.query(User).filter(User.role == "admin").first() is not None
        if not has_admin:
            from .auth import hash_password
            db.add(User(
                username=config.ADMIN_USER,
                password_hash=hash_password(config.ADMIN_PASS),
                role="admin",
            ))
            db.commit()
            log.info("seeded admin user %r from environment", config.ADMIN_USER)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.VMS_DIR.mkdir(parents=True, exist_ok=True)
    init_db()
    _maybe_seed_admin()
    db = SessionLocal()
    try:
        vm_manager.reconcile_on_startup(db)
    finally:
        db.close()
    log.info("vphone web ready — base VM: %s, binary: %s", config.BASE_VM_DIR, config.VPHONE_BIN)
    yield
    await webrtc.close_all()
    vm_manager.stop_all()


app = FastAPI(title="vphone web", lifespan=lifespan)
app.include_router(auth_router)
app.include_router(vm_router)
app.include_router(admin_router)
app.include_router(logs_router)
app.include_router(debug_router)


def _is_logs_host(request: Request) -> bool:
    """True when reached via the `logs.` subdomain (e.g. logs.vphone.local)."""
    host = (request.headers.get("host") or "").split(":")[0]
    return host.split(".")[0] == "logs"


@app.middleware("http")
async def logs_subdomain(request: Request, call_next):
    # On the logs subdomain, the site root serves the logs dashboard. API and
    # static routes are unchanged so the page can fetch data and assets.
    if request.url.path == "/" and _is_logs_host(request):
        resp = FileResponse(STATIC_DIR / "logs.html")
    else:
        resp = await call_next(request)
    # Never let browsers cache the app shell / assets — otherwise a UI update
    # leaves a stale app.js (missing new functions) and the page breaks.
    path = request.url.path
    if path.startswith("/static") or path in ("/", "/vm", "/admin", "/logs", "/login"):
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.get("/")
def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/logs")
def logs_page():
    return FileResponse(STATIC_DIR / "logs.html")


@app.get("/login")
def login_page():
    return FileResponse(STATIC_DIR / "login.html")


@app.get("/vm")
def vm_page():
    return FileResponse(STATIC_DIR / "vm.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
