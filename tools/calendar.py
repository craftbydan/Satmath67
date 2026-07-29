"""Google Calendar tool handlers: availability lookup and slot booking/cancelling.

Every call is executed in a worker thread via `asyncio.to_thread` since the
`google-api-python-client` client is synchronous.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from tools.google_auth import get_calendar_service

# Simple fixed business-hours window used to compute open slots. Good
# enough for Phase 3; a later phase could make this per-tenant.
WORK_DAY_START_HOUR = 9
WORK_DAY_END_HOUR = 18


async def check_teacher_availability(calendar_id: str, start_date: str, end_date: str) -> dict:
    return await asyncio.to_thread(_check_teacher_availability_sync, calendar_id, start_date, end_date)


def _check_teacher_availability_sync(calendar_id: str, start_date: str, end_date: str) -> dict:
    service = get_calendar_service()
    time_min = f"{start_date}T00:00:00Z"
    time_max = f"{end_date}T23:59:59Z"

    freebusy = service.freebusy().query(
        body={"timeMin": time_min, "timeMax": time_max, "items": [{"id": calendar_id}]}
    ).execute()
    calendar_info = freebusy.get("calendars", {}).get(calendar_id, {})
    if calendar_info.get("errors"):
        return {"error": f"Could not read calendar {calendar_id}: {calendar_info['errors']}"}

    busy_periods = [
        (_parse_dt(b["start"]), _parse_dt(b["end"])) for b in calendar_info.get("busy", [])
    ]
    slots = _compute_free_slots(start_date, end_date, busy_periods)
    return {"calendar_id": calendar_id, "start_date": start_date, "end_date": end_date, "available_slots": slots}


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _compute_free_slots(start_date: str, end_date: str, busy: list[tuple[datetime, datetime]]) -> list[dict]:
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)

    slots: list[dict] = []
    day = start
    while day <= end:
        day_start = datetime(day.year, day.month, day.day, WORK_DAY_START_HOUR, tzinfo=timezone.utc)
        day_end = datetime(day.year, day.month, day.day, WORK_DAY_END_HOUR, tzinfo=timezone.utc)

        free_ranges = [(day_start, day_end)]
        for busy_start, busy_end in busy:
            if busy_end <= day_start or busy_start >= day_end:
                continue
            next_ranges = []
            for range_start, range_end in free_ranges:
                if busy_end <= range_start or busy_start >= range_end:
                    next_ranges.append((range_start, range_end))
                    continue
                if busy_start > range_start:
                    next_ranges.append((range_start, min(busy_start, range_end)))
                if busy_end < range_end:
                    next_ranges.append((max(busy_end, range_start), range_end))
            free_ranges = next_ranges

        for range_start, range_end in free_ranges:
            if range_end > range_start:
                slots.append({"start": range_start.isoformat(), "end": range_end.isoformat()})

        day += timedelta(days=1)

    return slots


async def update_calendar_slot(
    calendar_id: str,
    action: str,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    summary: Optional[str] = None,
    event_id: Optional[str] = None,
) -> dict:
    """Book (create) or cancel (delete) a slot on the tutor's calendar. Admin only."""
    return await asyncio.to_thread(
        _update_calendar_slot_sync, calendar_id, action, start_time, end_time, summary, event_id
    )


def _update_calendar_slot_sync(
    calendar_id: str,
    action: str,
    start_time: Optional[str],
    end_time: Optional[str],
    summary: Optional[str],
    event_id: Optional[str],
) -> dict:
    service = get_calendar_service()

    if action == "book":
        if not start_time or not end_time:
            return {"error": "start_time and end_time (RFC3339) are required to book a slot."}
        event = {
            "summary": summary or "SAT Math Tutoring Session",
            "start": {"dateTime": start_time},
            "end": {"dateTime": end_time},
        }
        created = service.events().insert(calendarId=calendar_id, body=event).execute()
        return {
            "status": "booked",
            "event_id": created["id"],
            "html_link": created.get("htmlLink"),
            "start": start_time,
            "end": end_time,
        }

    if action == "cancel":
        if not event_id:
            return {"error": "event_id is required to cancel a slot."}
        service.events().delete(calendarId=calendar_id, eventId=event_id).execute()
        return {"status": "cancelled", "event_id": event_id}

    return {"error": f"Unknown action {action!r}; expected 'book' or 'cancel'."}


async def list_events_for_date(calendar_id: str, day: date, tz_name: str = "UTC") -> list[dict]:
    """Used by the Phase 4 daily digest job to list a tenant's sessions for `day`."""
    return await asyncio.to_thread(_list_events_for_date_sync, calendar_id, day, tz_name)


def _list_events_for_date_sync(calendar_id: str, day: date, tz_name: str) -> list[dict]:
    service = get_calendar_service()
    tz = ZoneInfo(tz_name)
    day_start = datetime(day.year, day.month, day.day, tzinfo=tz)
    day_end = day_start + timedelta(days=1)

    result = (
        service.events()
        .list(
            calendarId=calendar_id,
            timeMin=day_start.isoformat(),
            timeMax=day_end.isoformat(),
            singleEvents=True,
            orderBy="startTime",
        )
        .execute()
    )

    events = []
    for item in result.get("items", []):
        start = item.get("start", {}).get("dateTime") or item.get("start", {}).get("date")
        end = item.get("end", {}).get("dateTime") or item.get("end", {}).get("date")
        events.append({"summary": item.get("summary", "(no title)"), "start": start, "end": end})
    return events
