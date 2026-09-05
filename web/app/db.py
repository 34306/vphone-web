"""SQLite + SQLAlchemy setup."""
from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

config.DATA_DIR.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    f"sqlite:///{config.DB_PATH}",
    connect_args={"check_same_thread": False},
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401  (register models)
    Base.metadata.create_all(engine)
    _migrate()


def _migrate() -> None:
    """Lightweight additive migrations for columns added after a DB exists.
    create_all() never ALTERs existing tables, so add new columns by hand."""
    with engine.begin() as conn:
        cols = {row[1] for row in conn.execute(text("PRAGMA table_info(vms)"))}
        if "ios_version" not in cols:
            conn.execute(text("ALTER TABLE vms ADD COLUMN ios_version VARCHAR DEFAULT '26.1'"))

        ucols = {row[1] for row in conn.execute(text("PRAGMA table_info(users)"))}
        if "expires_at" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN expires_at DATETIME"))
        if "can_start" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN can_start INTEGER DEFAULT 0"))
        if "can_stop" not in ucols:
            conn.execute(text("ALTER TABLE users ADD COLUMN can_stop INTEGER DEFAULT 0"))


def get_db():
    """FastAPI dependency yielding a session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
