# pj — LINE Webhook (Phase 1)

Minimal multi-tenant-ready LINE Messaging API webhook for the "pj" SAT Math
AI assistant. Built on the official `line-bot-sdk` (v3) and FastAPI.

Phase 1 scope: verify webhook signatures, extract `destination` (tenant /
LINE Official Account ID), `source.userId`, and `message.text` from
incoming events, and echo the text back via the Reply API to prove the
end-to-end webhook flow works.

## Project structure

```
.
├── app/
│   ├── __init__.py
│   ├── config.py      # loads LINE credentials from environment
│   └── main.py        # FastAPI app + /webhook endpoint
├── .env.example
├── .gitignore
├── requirements.txt
└── README.md
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# edit .env and fill in LINE_CHANNEL_SECRET / LINE_CHANNEL_ACCESS_TOKEN
# from the LINE Developers Console (Messaging API channel -> Basic settings)
```

## Run locally

```bash
source .venv/bin/activate
uvicorn app.main:app --reload --port 8000
```

Health check: `curl http://localhost:8000/health` -> `{"status": "ok"}`

## Expose locally with ngrok and register the webhook

1. Start the server (above), then in another terminal:

   ```bash
   ngrok http 8000
   ```

2. Copy the `https://<random>.ngrok-free.app` URL ngrok prints.

3. In the LINE Developers Console, open your channel -> **Messaging API**
   tab -> **Webhook settings**:
   - Webhook URL: `https://<random>.ngrok-free.app/webhook`
   - Click **Verify** (should return 200 — with no signature header the
     server also validly rejects it as 400, verification handles this)
   - Turn **Use webhook** ON
   - Turn OFF **Auto-reply messages** and **Greeting messages** in
     Response settings so they don't interfere with the echo test

4. Add the bot as a friend using the QR code in the console, then send it
   a text message. You should receive the same text echoed back, and the
   server logs will print the extracted tenant/user/text:

   ```
   INFO:pj.webhook:Received message: tenant=U... user=U... text='hello pj'
   ```

## Notes for later phases

- `destination` (the LINE Official Account's own user ID) is logged on
  every request — this is the tenant key that will map to per-tenant
  config/routing once multiple Official Accounts are supported.
- Signature verification uses `WebhookParser` from `line-bot-sdk`, which
  validates the `X-Line-Signature` header against `LINE_CHANNEL_SECRET`
  using HMAC-SHA256, per the official LINE spec.
