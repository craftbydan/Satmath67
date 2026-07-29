import json
import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    ReplyMessageRequest,
    TextMessage,
)
from linebot.v3.webhook import WebhookParser
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from sqlalchemy import text as sql_text
from sqlmodel.ext.asyncio.session import AsyncSession

from app import crud, llm
from app.config import ENABLE_SCHEDULER
from app.db import engine, get_session, init_db

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pj.webhook")

app = FastAPI(title="pj LINE Webhook")


@app.on_event("startup")
async def on_startup() -> None:
    await init_db()

    # Off by default: see app/config.py ENABLE_SCHEDULER and the README's
    # "Background jobs in production" section for why this shouldn't be
    # turned on if the web service runs more than one instance/replica.
    if ENABLE_SCHEDULER:
        from app.scheduler import start_scheduler

        start_scheduler()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    if ENABLE_SCHEDULER:
        from app.scheduler import shutdown_scheduler

        shutdown_scheduler()


@app.get("/health")
async def health() -> dict:
    # Exercises the DB connection so uptime monitors (UptimeRobot, Better
    # Stack, Render's own health check) catch a broken DB, not just "the
    # process is up".
    try:
        async with engine.connect() as conn:
            await conn.execute(sql_text("SELECT 1"))
    except Exception:
        logger.exception("Health check DB connectivity failed")
        raise HTTPException(status_code=503, detail="database unavailable")

    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request, session: AsyncSession = Depends(get_session)):
    signature = request.headers.get("X-Line-Signature")
    if signature is None:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    raw_body = await request.body()
    body = raw_body.decode("utf-8")

    # `destination` identifies which tenant (LINE Official Account) this
    # webhook batch belongs to. We must read it before we can verify the
    # signature, because verification needs that tenant's own
    # channel_secret rather than a single global one.
    try:
        destination = json.loads(body).get("destination")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Malformed request body")

    tenant = await crud.get_tenant(session, destination) if destination else None
    if tenant is None:
        logger.warning("Webhook received for unknown tenant destination=%s", destination)
        return "OK"

    parser = WebhookParser(tenant.channel_secret)
    try:
        payload = parser.parse(body, signature, as_payload=True)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    line_config = Configuration(access_token=tenant.channel_access_token)
    async with AsyncApiClient(line_config) as async_api_client:
        messaging_api = AsyncMessagingApi(async_api_client)

        for event in payload.events:
            if not isinstance(event, MessageEvent):
                continue
            if not isinstance(event.message, TextMessageContent):
                continue

            user_id = event.source.user_id
            text = event.message.text

            user = await crud.get_or_create_user(session, tenant.id, user_id)

            logger.info(
                "Received message: tenant=%s user=%s role=%s text=%r",
                tenant.id,
                user_id,
                user.role.value,
                text,
            )

            await crud.save_message(session, tenant.id, user_id, "user", text)
            history = await crud.get_recent_history(session, tenant.id, user_id, limit=10)

            try:
                reply_text = await llm.generate_reply(
                    user.role, history, tenant=tenant, session=session, line_user_id=user_id
                )
            except Exception:
                logger.exception("LLM generation failed for tenant=%s user=%s", tenant.id, user_id)
                reply_text = "Sorry, I'm having trouble responding right now. Please try again shortly."

            await crud.save_message(session, tenant.id, user_id, "assistant", reply_text)

            await messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=reply_text)],
                )
            )

    return "OK"
