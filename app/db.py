from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlmodel import SQLModel
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Columns added after the initial Phase 2 schema. SQLModel's create_all only
# creates missing tables, not missing columns on existing ones, so a
# pre-existing `tenants` table needs these added explicitly.
_TENANT_COLUMNS_ADDED_IN_PHASE_3 = ("google_calendar_id", "google_drive_folder_id")


def _add_missing_tenant_columns(sync_conn) -> None:
    inspector = inspect(sync_conn)
    if "tenants" not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns("tenants")}
    for column_name in _TENANT_COLUMNS_ADDED_IN_PHASE_3:
        if column_name not in existing:
            sync_conn.execute(text(f"ALTER TABLE tenants ADD COLUMN {column_name} VARCHAR"))


async def init_db() -> None:
    # Imported for side effects: registers Tenant/User/Conversation with
    # SQLModel.metadata before create_all runs.
    from app import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.run_sync(_add_missing_tenant_columns)


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
