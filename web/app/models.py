"""ORM models: User, VM, and the user<->VM assignment link."""
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


vm_assignments = Table(
    "vm_assignments",
    Base.metadata,
    Column("user_id", ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("vm_id", ForeignKey("vms.id", ondelete="CASCADE"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="user")  # 'admin' | 'user'
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    # Access expiry (None = unlimited). Admins are always unlimited.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Power permissions for non-admins (admins can always start/stop).
    can_start: Mapped[bool] = mapped_column(Integer, default=0)
    can_stop: Mapped[bool] = mapped_column(Integer, default=0)

    vms: Mapped[list["VM"]] = relationship(
        secondary=vm_assignments, back_populates="users"
    )

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_expired(self) -> bool:
        if self.is_admin or self.expires_at is None:
            return False
        exp = self.expires_at
        if exp.tzinfo is None:  # stored naive UTC
            exp = exp.replace(tzinfo=timezone.utc)
        return _now() > exp

    @property
    def may_start(self) -> bool:
        return self.is_admin or bool(self.can_start)

    @property
    def may_stop(self) -> bool:
        return self.is_admin or bool(self.can_stop)


class VM(Base):
    __tablename__ = "vms"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    dir_name: Mapped[str] = mapped_column(String)  # subdir under VMS_DIR
    status: Mapped[str] = mapped_column(String, default="stopped")  # stopped|starting|running|error
    cpu: Mapped[int] = mapped_column(Integer, default=4)
    mem_mb: Mapped[int] = mapped_column(Integer, default=4096)
    ios_version: Mapped[str] = mapped_column(String, default="26.1")
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    users: Mapped[list[User]] = relationship(
        secondary=vm_assignments, back_populates="vms"
    )

    def to_dict(self, include_users: bool = False) -> dict:
        d = {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "cpu": self.cpu,
            "mem_mb": self.mem_mb,
            "ios_version": self.ios_version,
            "pid": self.pid,
        }
        if include_users:
            d["users"] = [{"id": u.id, "username": u.username} for u in self.users]
        return d
