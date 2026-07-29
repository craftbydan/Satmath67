import enum
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserRole(str, enum.Enum):
    student = "student"
    admin = "admin"


class RequestStatus(str, enum.Enum):
    pending = "pending"
    confirmed = "confirmed"
    cancelled = "cancelled"


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
    # Google Calendar ID and Drive folder ID this tenant's tools operate
    # against. The shared service account (see README) must be granted
    # access to both individually.
    google_calendar_id: Optional[str] = None
    google_drive_folder_id: Optional[str] = None


class User(SQLModel, table=True):
    """One row per (tenant, LINE user)."""

    __tablename__ = "users"
    __table_args__ = ({"sqlite_autoincrement": True},)

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    line_user_id: str = Field(index=True)
    role: UserRole = Field(default=UserRole.student)
    display_name: Optional[str] = None


class ScheduleRequest(SQLModel, table=True):
    """A student's requested session time, awaiting admin confirmation.

    Students can't book the calendar directly (see role scoping in
    tool_registry.py); this is the record the Phase 4 stale-session
    reminder job scans for pending, un-actioned requests.
    """

    __tablename__ = "schedule_requests"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    line_user_id: str = Field(index=True)
    requested_start: datetime
    requested_end: Optional[datetime] = None
    note: Optional[str] = None
    status: RequestStatus = Field(default=RequestStatus.pending, index=True)
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class Conversation(SQLModel, table=True):
    """One row per chat message (user or assistant) for a (tenant, user) thread.

    This is *short-term* memory: only the last 10 rows per user are ever
    fed back to the LLM (see crud.get_recent_history). For durable facts
    that should survive well past 10 messages, see `Memory` below.
    """

    __tablename__ = "conversations"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    line_user_id: str = Field(index=True)
    role: str  # "user" | "assistant" | "system"
    content: str
    created_at: datetime = Field(default_factory=_utcnow, index=True)


class Memory(SQLModel, table=True):
    """A durable fact pj has learned about a (tenant, user), independent of
    the rolling 10-message chat window.

    Design note: this is a from-scratch implementation of the same core
    pattern used by mem0 (github.com/mem0ai/mem0, the most widely-adopted
    open-source "memory layer for AI agents", ~48k stars) -- extract
    candidate facts from each turn, reconcile them against what's already
    stored (ADD / UPDATE / DELETE / NOOP) via an LLM call, and inject the
    current fact set into the system prompt on every future turn.

    We didn't take a dependency on the `mem0ai` package itself: its
    default local backend persists to an embedded Qdrant store on disk at
    `/tmp/qdrant`, and `/tmp` (and most container filesystems generally)
    is wiped on every redeploy/restart on Render and similar platforms --
    memory would silently vanish on every deploy. Running Qdrant properly
    would mean standing up a second stateful service just for this. Since
    we already have a Postgres database that *is* persisted correctly in
    production, and each user has at most a few dozen memory rows (no
    need for vector similarity search at that scale), plain rows here are
    simpler and correctly durable with zero new infrastructure. See
    app/memory.py for the extraction logic.
    """

    __tablename__ = "memories"

    id: Optional[int] = Field(default=None, primary_key=True)
    tenant_id: str = Field(foreign_key="tenants.id", index=True)
    line_user_id: str = Field(index=True)
    content: str
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
