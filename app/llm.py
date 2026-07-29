import json
import logging

from openai import AsyncOpenAI
from sqlmodel.ext.asyncio.session import AsyncSession

from app.config import OPENAI_API_KEY, OPENAI_MODEL
from app.models import Conversation, Tenant, UserRole
from app.tool_registry import execute_tool, tools_for_role

logger = logging.getLogger("pj.llm")

STUDENT_SYSTEM_PROMPT = (
    "You are pj, an SAT Math tutor assistant. Help answer student questions, "
    "check availability, and guide them."
)

ADMIN_SYSTEM_PROMPT = (
    "You are pj, the Admin Assistant for SAT Math Tutoring. Provide high-level "
    "schedule summaries, manage slots, and assist with teacher workflows."
)

# Hard cap on model <-> tool round trips per incoming message, so a
# confused model can't loop forever burning API calls.
MAX_TOOL_ITERATIONS = 4


def system_prompt_for_role(role: UserRole) -> str:
    return ADMIN_SYSTEM_PROMPT if role == UserRole.admin else STUDENT_SYSTEM_PROMPT


def _role_to_openai_role(role: str) -> str:
    return role if role in ("user", "assistant", "system") else "user"


async def generate_reply(
    role: UserRole,
    history: list[Conversation],
    *,
    tenant: Tenant,
    session: AsyncSession,
    line_user_id: str,
) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    messages: list[dict] = [{"role": "system", "content": system_prompt_for_role(role)}]
    messages.extend(
        {"role": _role_to_openai_role(m.role), "content": m.content} for m in history
    )

    tools = tools_for_role(role)

    for _ in range(MAX_TOOL_ITERATIONS):
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            messages=messages,
            tools=tools or None,
        )
        choice = response.choices[0].message

        if not choice.tool_calls:
            return choice.content or ""

        messages.append(
            {
                "role": "assistant",
                "content": choice.content,
                "tool_calls": [tc.model_dump() for tc in choice.tool_calls],
            }
        )

        for tool_call in choice.tool_calls:
            try:
                arguments = json.loads(tool_call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {}

            logger.info(
                "Tool call: tenant=%s role=%s tool=%s args=%s",
                tenant.id,
                role.value,
                tool_call.function.name,
                arguments,
            )
            result = await execute_tool(
                tool_call.function.name,
                arguments,
                session=session,
                tenant=tenant,
                role=role,
                line_user_id=line_user_id,
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": json.dumps(result),
                }
            )

    logger.warning("Tool loop exceeded %d iterations for tenant=%s", MAX_TOOL_ITERATIONS, tenant.id)
    return "I looked into that but couldn't finish -- could you try rephrasing your question?"
