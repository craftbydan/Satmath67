"""Long-term memory: durable facts pj remembers about a user, independent of
the rolling 10-message chat window in `conversations`.

Implements the same core pipeline as mem0 (github.com/mem0ai/mem0) --
extract candidate facts from the latest turn, reconcile them against what's
already stored via an LLM call that decides ADD / UPDATE / DELETE / NOOP per
fact, and apply the result. See `app/models.py::Memory` for why this is a
from-scratch implementation against our own Postgres/SQLite tables rather
than a dependency on the `mem0ai` package.
"""

import json
import logging

from openai import AsyncOpenAI
from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.config import OPENAI_API_KEY, OPENAI_MODEL
from app.models import Memory

logger = logging.getLogger("pj.memory")

EXTRACTION_SYSTEM_PROMPT = """\
You maintain long-term memory about one SAT Math tutoring user (a student \
or a tutoring admin), across many separate conversations.

Given the latest message exchange and the user's current list of \
remembered facts (each with a numeric id), decide what should be \
remembered going forward. Only keep durable, useful facts: stated \
preferences, goals, recurring scheduling patterns, strong/weak math \
topics, or other personal context relevant to tutoring. Do NOT store \
one-off small talk, greetings, or anything that's just a rephrasing of an \
existing memory.

Respond with strict JSON only, no prose, in this shape:
{"operations": [
  {"action": "ADD", "content": "<new fact, concise, one sentence>"},
  {"action": "UPDATE", "id": <existing memory id>, "content": "<replacement text>"},
  {"action": "DELETE", "id": <existing memory id>},
  {"action": "NOOP"}
]}

Use UPDATE when new information supersedes an existing memory (reuse its \
id). Use DELETE when a memory is now outdated or contradicted. If nothing \
in this exchange is worth remembering, return {"operations": [{"action": "NOOP"}]}.
Never invent a memory id that wasn't given to you.
"""


def _format_existing_memories(memories: list[Memory]) -> str:
    if not memories:
        return "(none yet)"
    return "\n".join(f"- id={m.id}: {m.content}" for m in memories)


async def extract_and_update_memories(
    session: AsyncSession,
    tenant_id: str,
    line_user_id: str,
    user_text: str,
    assistant_text: str,
) -> None:
    """Best-effort: never let a memory-extraction failure break the reply path."""
    if not OPENAI_API_KEY:
        return

    try:
        existing = await crud.list_memories(session, tenant_id, line_user_id)

        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model=OPENAI_MODEL,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Current memories:\n{_format_existing_memories(existing)}\n\n"
                        f"Latest exchange:\nUser: {user_text}\nAssistant: {assistant_text}"
                    ),
                },
            ],
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        operations = payload.get("operations", [])

        existing_ids = {m.id for m in existing}
        for op in operations:
            action = op.get("action")
            if action == "ADD" and op.get("content"):
                await crud.add_memory(session, tenant_id, line_user_id, op["content"])
            elif action == "UPDATE" and op.get("id") in existing_ids and op.get("content"):
                await crud.update_memory(session, op["id"], op["content"])
            elif action == "DELETE" and op.get("id") in existing_ids:
                await crud.delete_memory(session, op["id"])
            # NOOP, or anything malformed (bad id, missing content): skip silently.

    except Exception:
        logger.exception(
            "Memory extraction failed: tenant=%s user=%s", tenant_id, line_user_id
        )


def format_memories_for_prompt(memories: list[Memory]) -> str:
    if not memories:
        return ""
    facts = "\n".join(f"- {m.content}" for m in memories)
    return f"\n\nWhat you remember about this user from past conversations:\n{facts}"
