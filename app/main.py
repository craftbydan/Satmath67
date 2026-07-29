import logging

from fastapi import FastAPI, HTTPException, Request
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

from app.config import LINE_CHANNEL_ACCESS_TOKEN, LINE_CHANNEL_SECRET

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("pj.webhook")

app = FastAPI(title="pj LINE Webhook")

parser = WebhookParser(LINE_CHANNEL_SECRET)
line_config = Configuration(access_token=LINE_CHANNEL_ACCESS_TOKEN)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("X-Line-Signature")
    if signature is None:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    body = (await request.body()).decode("utf-8")

    try:
        payload = parser.parse(body, signature, as_payload=True)
    except InvalidSignatureError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    # destination = the LINE Official Account (tenant) this webhook batch was sent to.
    # Shared by all events in the payload; used for (tenant, user) routing in later phases.
    tenant_id = payload.destination

    async with AsyncApiClient(line_config) as async_api_client:
        messaging_api = AsyncMessagingApi(async_api_client)

        for event in payload.events:
            if not isinstance(event, MessageEvent):
                continue
            if not isinstance(event.message, TextMessageContent):
                continue

            # source.user_id = the end user's LINE user ID.
            user_id = event.source.user_id
            text = event.message.text

            logger.info(
                "Received message: tenant=%s user=%s text=%r", tenant_id, user_id, text
            )

            await messaging_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,
                    messages=[TextMessage(text=text)],
                )
            )

    return "OK"
