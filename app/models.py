import enum
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    student = "student"
    admin = "admin"


class Tenant(SQLModel, table=True):
    """One row per LINE Official Account. `id` is the LINE `destination` ID."""

    __tablename__ = "tenants"

    id: str = Field(primary_key=True)
    name: str
    channel_access_token: str
    # Required to verify webhook signatures per-tenant (not in the original
    # spec's field list, but signature verification is impossible without
    # it once more than one Official Account shares this service).
    channel_secret: str
    folder_path: Optional[str] = None


class User(SQLModel, table=True):
    """One row per (tenant, LINE user)."""

    __tablename__ = "users"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    line_user_id: str = Field(index=True)
    role: UserRole = Field(default=UserRole.student)
    display_name: Optional[str] = None


class Conversation(SQLModel, table=True):
    """One row per chat message (user or assistant) for a (tenant, user) thread."""

    __tablename__ = "conversations"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    line_user_id: str = Field(index=True)
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: datetime = Field(default_factory=_utcnow, index=True)
