from typing import Optional

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models import Conversation, Tenant, User, UserRole


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
