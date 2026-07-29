"""Role-scoped tool schemas (for OpenAI function calling) and dispatch.

`calendar_id` / `folder_id` are never accepted as LLM-supplied arguments --
they're injected here from the tenant record, so the model can only ever
touch the calling tenant's own calendar/Drive folder.
"""

import logging
from datetime import datetime
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud
from app.models import RequestStatus, Tenant, UserRole
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
    "request_session_time": {
        "type": "function",
        "function": {
            "name": "request_session_time",
            "description": (
                "Request a specific SAT Math tutoring session time. This does NOT book "
                "anything -- it logs a pending request for an admin to confirm or "
                "decline (students can't book the calendar directly)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "requested_start": {
                        "type": "string",
                        "description": "RFC3339 datetime you'd like to start",
                    },
                    "requested_end": {
                        "type": "string",
                        "description": "RFC3339 datetime you'd like to end, optional",
                    },
                    "note": {"type": "string", "description": "Any context for the admin, optional"},
                },
                "required": ["requested_start"],
            },
        },
    },
    "list_pending_requests": {
        "type": "function",
        "function": {
            "name": "list_pending_requests",
            "description": "List all pending (unconfirmed) session requests for this tenant. Admin only.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    "resolve_schedule_request": {
        "type": "function",
        "function": {
            "name": "resolve_schedule_request",
            "description": (
                "Mark a pending session request as confirmed or cancelled. Does not "
                "itself create a calendar event -- call update_calendar_slot separately "
                "to actually book it. Admin only."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "request_id": {"type": "integer"},
                    "status": {"type": "string", "enum": ["confirmed", "cancelled"]},
                },
                "required": ["request_id", "status"],
            },
        },
    },
}

ROLE_TOOLS: dict[UserRole, list[str]] = {
    UserRole.student: [
        "check_teacher_availability",
        "search_student_materials",
        "request_session_time",
    ],
    UserRole.admin: [
        "check_teacher_availability",
        "search_student_materials",
        "update_calendar_slot",
        "upload_material",
        "get_student_summary",
        "list_pending_requests",
        "resolve_schedule_request",
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
    line_user_id: str,
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

        if name == "request_session_time":
            try:
                requested_start = datetime.fromisoformat(arguments["requested_start"])
            except (KeyError, ValueError):
                return {"error": "requested_start must be a valid RFC3339/ISO datetime."}
            requested_end = None
            if arguments.get("requested_end"):
                try:
                    requested_end = datetime.fromisoformat(arguments["requested_end"])
                except ValueError:
                    return {"error": "requested_end must be a valid RFC3339/ISO datetime."}
            request = await crud.create_schedule_request(
                session,
                tenant.id,
                line_user_id,
                requested_start,
                requested_end,
                arguments.get("note"),
            )
            return {
                "status": "requested",
                "request_id": request.id,
                "requested_start": arguments["requested_start"],
            }

        if name == "list_pending_requests":
            pending = await crud.list_pending_schedule_requests(session, tenant.id)
            return {
                "tenant_id": tenant.id,
                "pending_count": len(pending),
                "requests": [
                    {
                        "id": r.id,
                        "line_user_id": r.line_user_id,
                        "requested_start": r.requested_start.isoformat(),
                        "requested_end": r.requested_end.isoformat() if r.requested_end else None,
                        "note": r.note,
                        "created_at": r.created_at.isoformat(),
                    }
                    for r in pending
                ],
            }

        if name == "resolve_schedule_request":
            request_id = arguments.get("request_id")
            status_str = arguments.get("status")
            if request_id is None or status_str not in ("confirmed", "cancelled"):
                return {"error": "request_id and status ('confirmed'|'cancelled') are required."}
            updated = await crud.resolve_schedule_request(session, request_id, RequestStatus(status_str))
            if updated is None or updated.tenant_id != tenant.id:
                return {"error": f"No schedule request with id {request_id} for this tenant."}
            return {"status": "updated", "request_id": updated.id, "new_status": updated.status.value}

        return {"error": f"Unknown tool '{name}'."}
    except KeyError as exc:
        return {"error": f"Missing required argument {exc} for tool '{name}'."}
    except Exception as exc:  # Google API errors, etc.
        logger.exception("Tool execution failed: tool=%s tenant=%s", name, tenant.id)
        return {"error": f"Tool '{name}' failed: {exc}"}
