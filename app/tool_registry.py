"""Role-scoped tool schemas (for OpenAI function calling) and dispatch.

`calendar_id` / `folder_id` are never accepted as LLM-supplied arguments --
they're injected here from the tenant record, so the model can only ever
touch the calling tenant's own calendar/Drive folder.
"""

import logging
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.models import Tenant, UserRole
from tools import calendar as calendar_tools
from tools import drive as drive_tools

logger = logging.getLogger("pj.tools")

TOOL_SCHEMAS: dict[str, dict] = {
    "check_teacher_availability": {
        "type": "function",
        "function": {
            "name": "check_teacher_availability",
            "description": (
                "Check the tutor's Google Calendar for open (free) slots in a date "
                "range. Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "start_date": {"type": "string", "description": "Start date, YYYY-MM-DD"},
                    "end_date": {"type": "string", "description": "End date, YYYY-MM-DD"},
                },
                "required": ["start_date", "end_date"],
            },
        },
    },
    "search_student_materials": {
        "type": "function",
        "function": {
            "name": "search_student_materials",
            "description": (
                "Search this tenant's Google Drive materials folder for PDFs/problem "
                "sets matching keywords, e.g. 'Advanced Algebra' or 'Practice Test 1'. "
                "Read-only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "keywords": {"type": "string"},
                },
                "required": ["keywords"],
            },
        },
    },
    "update_calendar_slot": {
        "type": "function",
        "function": {
            "name": "update_calendar_slot",
            "description": (
                "Book (create) or cancel (delete) a tutoring slot on the tutor's "
                "Google Calendar. Admin only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["book", "cancel"]},
                    "start_time": {
                        "type": "string",
                        "description": "RFC3339 datetime, required to book",
                    },
                    "end_time": {
                        "type": "string",
                        "description": "RFC3339 datetime, required to book",
                    },
                    "summary": {"type": "string", "description": "Event title, optional"},
                    "event_id": {
                        "type": "string",
                        "description": "Existing event ID, required to cancel",
                    },
                },
                "required": ["action"],
            },
        },
    },
    "upload_material": {
        "type": "function",
        "function": {
            "name": "upload_material",
            "description": (
                "Upload a new text-based note/material into this tenant's Drive "
                "materials folder. Admin only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "content_text": {"type": "string"},
                },
                "required": ["filename", "content_text"],
            },
        },
    },
    "get_student_summary": {
        "type": "function",
        "function": {
            "name": "get_student_summary",
            "description": "Get a roster/summary of students registered for this tenant. Admin only.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
}

ROLE_TOOLS: dict[UserRole, list[str]] = {
    UserRole.student: ["check_teacher_availability", "search_student_materials"],
    UserRole.admin: [
        "check_teacher_availability",
        "search_student_materials",
        "update_calendar_slot",
        "upload_material",
        "get_student_summary",
    ],
}


def tools_for_role(role: UserRole) -> list[dict]:
    return [TOOL_SCHEMAS[name] for name in ROLE_TOOLS.get(role, [])]


async def execute_tool(
    name: str,
    arguments: dict[str, Any],
    *,
    session: AsyncSession,
    tenant: Tenant,
    role: UserRole,
) -> dict:
    if name not in ROLE_TOOLS.get(role, []):
        logger.warning("Blocked tool call: role=%s tenant=%s tool=%s", role.value, tenant.id, name)
        return {"error": f"Tool '{name}' is not permitted for role '{role.value}'."}

    try:
        if name == "check_teacher_availability":
            if not tenant.google_calendar_id:
                return {"error": "This tenant has no google_calendar_id configured."}
            return await calendar_tools.check_teacher_availability(
                tenant.google_calendar_id, arguments["start_date"], arguments["end_date"]
            )

        if name == "search_student_materials":
            if not tenant.google_drive_folder_id:
                return {"error": "This tenant has no google_drive_folder_id configured."}
            return await drive_tools.search_student_materials(
                tenant.google_drive_folder_id, arguments["keywords"]
            )

        if name == "update_calendar_slot":
            if not tenant.google_calendar_id:
                return {"error": "This tenant has no google_calendar_id configured."}
            return await calendar_tools.update_calendar_slot(
                tenant.google_calendar_id,
                action=arguments["action"],
                start_time=arguments.get("start_time"),
                end_time=arguments.get("end_time"),
                summary=arguments.get("summary"),
                event_id=arguments.get("event_id"),
            )

        if name == "upload_material":
            if not tenant.google_drive_folder_id:
                return {"error": "This tenant has no google_drive_folder_id configured."}
            return await drive_tools.upload_material(
                tenant.google_drive_folder_id, arguments["filename"], arguments["content_text"]
            )

        if name == "get_student_summary":
            students = await crud.list_students(session, tenant.id)
            return {
                "tenant_id": tenant.id,
                "student_count": len(students),
                "students": [
                    {"line_user_id": s.line_user_id, "display_name": s.display_name}
                    for s in students
                ],
            }

        return {"error": f"Unknown tool '{name}'."}
    except KeyError as exc:
        return {"error": f"Missing required argument {exc} for tool '{name}'."}
    except Exception as exc:  # Google API errors, etc.
        logger.exception("Tool execution failed: tool=%s tenant=%s", name, tenant.id)
        return {"error": f"Tool '{name}' failed: {exc}"}
