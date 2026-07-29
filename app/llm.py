from openai import AsyncOpenAI

from app.config import OPENAI_API_KEY, OPENAI_MODEL
from app.models import Conversation, UserRole

STUDENT_SYSTEM_PROMPT = (
    "You are pj, an SAT Math tutor assistant. Help answer student questions, "
    "check availability, and guide them."
)

ADMIN_SYSTEM_PROMPT = (
    "You are pj, the Admin Assistant for SAT Math Tutoring. Provide high-level "
    "schedule summaries, manage slots, and assist with teacher workflows."
)


def system_prompt_for_role(role: UserRole) -> str:
    return ADMIN_SYSTEM_PROMPT if role == UserRole.admin else STUDENT_SYSTEM_PROMPT


def _role_to_openai_role(role: str) -> str:
    return role if role in ("user", "assistant", "system") else "user"


async def generate_reply(role: UserRole, history: list[Conversation]) -> str:
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")

    client = AsyncOpenAI(api_key=OPENAI_API_KEY)

    messages = [{"role": "system", "content": system_prompt_for_role(role)}]
    messages.extend(
        {"role": _role_to_openai_role(m.role), "content": m.content} for m in history
    )

    response = await client.chat.completions.create(
        model=OPENAI_MODEL,
        messages=messages,
    )
    return response.choices[0].message.content or ""
