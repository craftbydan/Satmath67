"""Phase 4 background jobs: daily admin digest and stale-request reminders.

Two ways to run these (see README "Background jobs in production"):

1. One-shot, via `run_job.py` -- intended for Render's native Cron Jobs (or
   any external scheduler/webhook). Each invocation runs a job once and
   exits; the scheduling itself is owned by the platform, so nothing here
   needs to stay running and there's no risk of duplicate firing across
   replicas.
2. Always-on, via `worker.py` (or embedded in the web process with
   ENABLE_SCHEDULER=true) -- uses APScheduler's AsyncIOScheduler, defined
   below, for platforms without native cron (Railway, Fly, a bare VPS).
   Only ever run this in a single process at a time.
"""

import logging
from datetime import date
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    PushMessageRequest,
    TextMessage,
)

from app import crud
from app.config import DIGEST_TIMEZONE, STALE_REQUEST_HOURS
from app.db import async_session
from app.models import Tenant
from tools import calendar as calendar_tools

logger = logging.getLogger("pj.scheduler")


async def _push_to_admins(tenant: Tenant, text: str) -> None:
    async with async_session() as session:
        admins = await crud.list_admins(session, tenant.id)
    if not admins:
        return

    line_config = Configuration(access_token=tenant.channel_access_token)
    async with AsyncApiClient(line_config) as api_client:
        messaging_api = AsyncMessagingApi(api_client)
        for admin in admins:
            try:
                await messaging_api.push_message(
                    PushMessageRequest(to=admin.line_user_id, messages=[TextMessage(text=text)])
                )
            except Exception:
                logger.exception(
                    "Failed to push message to admin: tenant=%s user=%s", tenant.id, admin.line_user_id
                )


async def send_daily_digest() -> None:
    """8:00 AM job: today's calendar sessions, pushed to every admin per tenant."""
    async with async_session() as session:
        tenants = await crud.list_all_tenants(session)

    today = date.today()

    for tenant in tenants:
        if not tenant.google_calendar_id:
            continue

        try:
            events = await calendar_tools.list_events_for_date(
                tenant.google_calendar_id, today, DIGEST_TIMEZONE
            )
        except Exception:
            logger.exception("Failed to fetch today's events for tenant=%s", tenant.id)
            continue

        if events:
            lines = [f"- {e['start']} to {e['end']}: {e['summary']}" for e in events]
            body = "Good morning! Today's SAT Math schedule:\n" + "\n".join(lines)
        else:
            body = "Good morning! No SAT Math sessions are scheduled today."

        logger.info("Sending daily digest: tenant=%s events=%d", tenant.id, len(events))
        await _push_to_admins(tenant, body)


async def check_stale_requests() -> None:
    """Periodic job: nudge admins about session requests still unconfirmed."""
    async with async_session() as session:
        tenants = await crud.list_all_tenants(session)

    for tenant in tenants:
        async with async_session() as session:
            stale = await crud.list_pending_schedule_requests(session, tenant.id, STALE_REQUEST_HOURS)
        if not stale:
            continue

        lines = [
            f"- #{r.id} from {r.line_user_id}: wants {r.requested_start.isoformat()}"
            + (f" ({r.note})" if r.note else "")
            for r in stale
        ]
        body = (
            f"⚠️ {len(stale)} session request(s) have been pending "
            f"{STALE_REQUEST_HOURS:g}+ hours:\n" + "\n".join(lines)
        )
        logger.info("Stale request reminder: tenant=%s count=%d", tenant.id, len(stale))
        await _push_to_admins(tenant, body)


scheduler = AsyncIOScheduler()


def start_scheduler() -> None:
    if scheduler.running:
        return
    scheduler.add_job(
        send_daily_digest,
        CronTrigger(hour=8, minute=0, timezone=ZoneInfo(DIGEST_TIMEZONE)),
        id="daily_digest",
        replace_existing=True,
    )
    scheduler.add_job(
        check_stale_requests,
        IntervalTrigger(hours=1),
        id="stale_request_reminder",
        replace_existing=True,
    )
    scheduler.start()
    logger.info(
        "In-process scheduler started: daily digest at 08:00 %s, stale-request check hourly",
        DIGEST_TIMEZONE,
    )


def shutdown_scheduler() -> None:
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("In-process scheduler stopped")
