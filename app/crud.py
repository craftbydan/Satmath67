from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Conversation, Memory, RequestStatus, ScheduleRequest, Tenant, User, UserRole


async def get_tenant(session: AsyncSession, tenant_id: str) -> Optional[Tenant]:
    return await session.get(Tenant, tenant_id)


async def get_or_create_user(
    session: AsyncSession,
    tenant_id: str,
    line_user_id: str,
    display_name: Optional[str] = None,
) -> User:
    result = await session.exec(
        select(User).where(User.tenant_id == tenant_id, User.line_user_id == line_user_id)
    )
    user = result.first()
    if user is not None:
        if display_name and user.display_name != display_name:
            user.display_name = display_name
            session.add(user)
            await session.commit()
            await session.refresh(user)
        return user

    user = User(
        tenant_id=tenant_id,
        line_user_id=line_user_id,
        role=UserRole.student,
        display_name=display_name,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def list_students(session: AsyncSession, tenant_id: str) -> list[User]:
    result = await session.exec(
        select(User).where(User.tenant_id == tenant_id, User.role == UserRole.student)
    )
    return list(result.all())


async def list_admins(session: AsyncSession, tenant_id: str) -> list[User]:
    result = await session.exec(
        select(User).where(User.tenant_id == tenant_id, User.role == UserRole.admin)
    )
    return list(result.all())


async def list_all_tenants(session: AsyncSession) -> list[Tenant]:
    result = await session.exec(select(Tenant))
    return list(result.all())


async def create_schedule_request(
    session: AsyncSession,
    tenant_id: str,
    line_user_id: str,
    requested_start: datetime,
    requested_end: Optional[datetime] = None,
    note: Optional[str] = None,
) -> ScheduleRequest:
    request = ScheduleRequest(
        tenant_id=tenant_id,
        line_user_id=line_user_id,
        requested_start=requested_start,
        requested_end=requested_end,
        note=note,
    )
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request


async def list_pending_schedule_requests(
    session: AsyncSession, tenant_id: str, older_than_hours: float = 0
) -> list[ScheduleRequest]:
    """Pending requests created at least `older_than_hours` ago (0 = all pending)."""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=older_than_hours)
    result = await session.exec(
        select(ScheduleRequest)
        .where(
            ScheduleRequest.tenant_id == tenant_id,
            ScheduleRequest.status == RequestStatus.pending,
            ScheduleRequest.created_at <= cutoff,
        )
        .order_by(ScheduleRequest.created_at.asc())
    )
    return list(result.all())


async def resolve_schedule_request(
    session: AsyncSession, request_id: int, status: RequestStatus
) -> Optional[ScheduleRequest]:
    request = await session.get(ScheduleRequest, request_id)
    if request is None:
        return None
    request.status = status
    session.add(request)
    await session.commit()
    await session.refresh(request)
    return request


async def save_message(
    session: AsyncSession, tenant_id: str, line_user_id: str, role: str, content: str
) -> Conversation:
    message = Conversation(
        tenant_id=tenant_id, line_user_id=line_user_id, role=role, content=content
    )
    session.add(message)
    await session.commit()
    await session.refresh(message)
    return message


async def get_recent_history(
    session: AsyncSession, tenant_id: str, line_user_id: str, limit: int = 10
) -> list[Conversation]:
    result = await session.exec(
        select(Conversation)
        .where(Conversation.tenant_id == tenant_id, Conversation.line_user_id == line_user_id)
        .order_by(Conversation.created_at.desc())
        .limit(limit)
    )
    messages = list(result.all())
    messages.reverse()  # chronological order for the LLM
    return messages


async def list_memories(session: AsyncSession, tenant_id: str, line_user_id: str) -> list[Memory]:
    result = await session.exec(
        select(Memory)
        .where(Memory.tenant_id == tenant_id, Memory.line_user_id == line_user_id)
        .order_by(Memory.updated_at.asc())
    )
    return list(result.all())


async def add_memory(session: AsyncSession, tenant_id: str, line_user_id: str, content: str) -> Memory:
    memory = Memory(tenant_id=tenant_id, line_user_id=line_user_id, content=content)
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


async def update_memory(session: AsyncSession, memory_id: int, content: str) -> Optional[Memory]:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        return None
    memory.content = content
    memory.updated_at = datetime.now(timezone.utc)
    session.add(memory)
    await session.commit()
    await session.refresh(memory)
    return memory


async def delete_memory(session: AsyncSession, memory_id: int) -> bool:
    memory = await session.get(Memory, memory_id)
    if memory is None:
        return False
    await session.delete(memory)
    await session.commit()
    return True
